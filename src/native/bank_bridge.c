/* Mechanical JSON, IBM CLI and OpenSSL bindings. No banking policy or SQL. */
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <json-c/json.h>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <sqlcli1.h>

#define FIELD_SIZE 1024
#define COLUMN_LIMIT 32
static SQLHENV environment;
static SQLHDBC connection;
static SQLHSTMT statement;
static SQLLEN indicators[16];
static char parameters[16][FIELD_SIZE];
static char columns[COLUMN_LIMIT][FIELD_SIZE];
static char column_names[COLUMN_LIMIT][128];
static SQLSMALLINT column_types[COLUMN_LIMIT], column_count;
static int null_columns[COLUMN_LIMIT];
static json_object *request, *response, *result_rows;
static FILE *migration;
static int worker_mode, worker_invalid;
static char worker_buffer[16385];
static size_t worker_length;

static int successful(SQLRETURN code) { return code == SQL_SUCCESS || code == SQL_SUCCESS_WITH_INFO; }
static size_t field_length(const char *text, size_t size) {
    while (size && text[size - 1] == ' ') --size;
    return size;
}
static void copy_field(char *target, size_t size, const char *value) {
    size_t length = strlen(value);
    memset(target, ' ', size);
    memcpy(target, value, length < size ? length : size);
}
static void trim_field(char *target, const char *source, size_t size) {
    size_t length = field_length(source, size);
    memcpy(target, source, length);
    target[length] = 0;
}

int bank_worker_next(void) {
    worker_mode = 1;
    worker_length = 0;
    worker_invalid = 0;
    int character;
    while ((character = getchar()) != EOF && character != '\n') {
        if (worker_length < sizeof(worker_buffer) - 1)
            worker_buffer[worker_length++] = (char)character;
        else worker_invalid = 1;
    }
    if (character == EOF && !worker_length && !worker_invalid) return 2;
    if (character == EOF || worker_length >= sizeof(worker_buffer) - 1) worker_invalid = 1;
    worker_buffer[worker_length] = 0;
    return 0;
}
int bank_input(void) {
    response = json_object_new_object();
    result_rows = json_object_new_array();
    json_object_object_add(response, "rows", result_rows);
    char buffer[16385];
    size_t length;
    if (worker_mode) {
        length = worker_length;
        memcpy(buffer, worker_buffer, length);
        OPENSSL_cleanse(worker_buffer, sizeof(worker_buffer));
        if (worker_invalid) return 1;
    } else {
        length = fread(buffer, 1, sizeof(buffer) - 1, stdin);
        if (length == sizeof(buffer) - 1 || ferror(stdin)) return 1;
    }
    buffer[length] = 0;
    json_tokener *parser = json_tokener_new();
    json_tokener_set_flags(parser, JSON_TOKENER_STRICT | JSON_TOKENER_VALIDATE_UTF8);
    request = json_tokener_parse_ex(parser, buffer, (int)length);
    size_t end = json_tokener_get_parse_end(parser);
    int valid = json_tokener_get_error(parser) == json_tokener_success &&
                request && json_object_is_type(request, json_type_object);
    while (end < length && isspace((unsigned char)buffer[end])) end++;
    json_tokener_free(parser);
    OPENSSL_cleanse(buffer, sizeof(buffer));
    return !valid || end != length;
}
int bank_field(const char *key, char *output, int size) {
    json_object *value;
    memset(output, ' ', (size_t)size);
    if (!json_object_object_get_ex(request, key, &value)) return 0;
    if (!json_object_is_type(value, json_type_string)) return -1;
    const char *text = json_object_get_string(value);
    size_t length = (size_t)json_object_get_string_len(value);
    if (length > (size_t)size || strlen(text) != length) return -1;
    memcpy(output, text, length);
    return (int)length;
}
int bank_text(const char *key, const char *value, int size) {
    json_object_object_add(response, key, json_object_new_string_len(value, (int)field_length(value, (size_t)size)));
    return 0;
}
int bank_finish(const char *error, int size) {
    size_t length = field_length(error, (size_t)size);
    json_object_object_add(response, "ok", json_object_new_boolean(!length));
    if (length) {
        /* Discard partial result sets if a transaction failed. */
        json_object_put(response);
        response = json_object_new_object();
        json_object_object_add(response, "ok", json_object_new_boolean(0));
        json_object_object_add(response, "error", json_object_new_string_len(error, (int)length));
    }
    puts(json_object_to_json_string_ext(response, JSON_C_TO_STRING_PLAIN));
    fflush(stdout);
    json_object_put(request);
    json_object_put(response);
    request = response = result_rows = NULL;
    if (migration) { fclose(migration); migration = NULL; }
    OPENSSL_cleanse(parameters, sizeof(parameters));
    OPENSSL_cleanse(columns, sizeof(columns));
    return length ? 1 : 0;
}

int bank_db_discard(void);
int bank_db_open(void) {
    if (worker_mode && connection) return 0;
    const char *keys[] = {"DB2_DATABASE", "DB2_HOST", "DB2_USER", "DB2_PASSWORD"};
    const char *values[4];
    for (int index = 0; index < 4; index++) {
        values[index] = getenv(keys[index]);
        if (!values[index] || !*values[index] || strlen(values[index]) > 254 ||
            strpbrk(values[index], ";{}\r\n")) return 1;
    }
    const char *schema = getenv("DB2_SCHEMA");
    if (schema) {
        size_t length = strlen(schema);
        if (!length || length > 128) return 1;
        for (size_t index = 0; index < length; index++)
            if (!(schema[index] >= 'A' && schema[index] <= 'Z') &&
                !(schema[index] >= '0' && schema[index] <= '9') && schema[index] != '_') return 1;
    }
    char dsn[1400];
    snprintf(dsn, sizeof(dsn), "DATABASE=%s;HOSTNAME=%s;PORT=50000;PROTOCOL=TCPIP;UID=%s;PWD=%s;CONNECTTIMEOUT=10;",
             values[0], values[1], values[2], values[3]);
    if (schema) {
        size_t used = strlen(dsn);
        snprintf(dsn + used, sizeof(dsn) - used, "CURRENTSCHEMA=%s;", schema);
    }
    if (!successful(SQLAllocHandle(SQL_HANDLE_ENV, SQL_NULL_HANDLE, &environment))) { bank_db_discard(); return 1; }
    if (!successful(SQLSetEnvAttr(environment, SQL_ATTR_ODBC_VERSION, (SQLPOINTER)SQL_OV_ODBC3, 0))) { bank_db_discard(); return 1; }
    if (!successful(SQLAllocHandle(SQL_HANDLE_DBC, environment, &connection))) { bank_db_discard(); return 1; }
    SQLRETURN code = SQLDriverConnect(connection, NULL, (SQLCHAR *)dsn, SQL_NTS, NULL, 0, NULL, SQL_DRIVER_NOPROMPT);
    OPENSSL_cleanse(dsn, sizeof(dsn));
    if (!successful(code)) { bank_db_discard(); return 1; }
    if (!successful(SQLSetConnectAttr(connection, SQL_ATTR_AUTOCOMMIT, (SQLPOINTER)SQL_AUTOCOMMIT_OFF, 0))) {
        bank_db_discard();
        return 1;
    }
    return 0;
}
static int statement_error(const char *operation) {
    SQLCHAR state[6], message[512];
    SQLINTEGER code;
    SQLSMALLINT length;
    if (successful(SQLGetDiagRec(SQL_HANDLE_STMT, statement, 1, state, &code,
                                message, sizeof(message), &length))) {
        /* Never emit driver messages: they can contain SQL values or secrets. */
        fprintf(stderr, "Db2 %s failed: SQLSTATE=%.5s code=%d\n", operation, state, (int)code);
    }
    return 1;
}
int bank_db_prepare(const char *sql, int size) {
    if (statement) { SQLFreeHandle(SQL_HANDLE_STMT, statement); statement = 0; }
    if (!successful(SQLAllocHandle(SQL_HANDLE_STMT, connection, &statement))) return 1;
    if (!successful(SQLSetStmtAttr(statement, SQL_ATTR_QUERY_TIMEOUT, (SQLPOINTER)(intptr_t)10, 0))) return 1;
    column_count = 0;
    return successful(SQLPrepare(statement, (SQLCHAR *)sql, (SQLINTEGER)field_length(sql, (size_t)size))) ? 0 : statement_error("prepare");
}
int bank_db_bind(int index, const char *value, int size, int is_null) {
    if (index < 1 || index > 16 || size >= FIELD_SIZE) return 1;
    int slot = index - 1;
    trim_field(parameters[slot], value, (size_t)size);
    indicators[slot] = is_null ? SQL_NULL_DATA : (SQLLEN)strlen(parameters[slot]);
    return !successful(SQLBindParameter(statement, (SQLUSMALLINT)index, SQL_PARAM_INPUT,
                       SQL_C_CHAR, SQL_VARCHAR, (SQLULEN)(indicators[slot] > 0 ? indicators[slot] : 1),
                       0, parameters[slot], FIELD_SIZE, &indicators[slot]));
}
int bank_db_execute(void) {
    SQLRETURN code = SQLExecute(statement);
    /* A searched update/delete affecting zero rows is a valid operation. */
    if (!successful(code) && code != SQL_NO_DATA) return statement_error("execute");
    if (!successful(SQLNumResultCols(statement, &column_count)) || column_count > COLUMN_LIMIT) return 1;
    for (int index = 0; index < column_count; index++) {
        SQLSMALLINT length, decimal_digits, nullable;
        SQLULEN size;
        if (!successful(SQLDescribeCol(statement, (SQLUSMALLINT)(index + 1), (SQLCHAR *)column_names[index],
                        sizeof(column_names[index]), &length, &column_types[index], &size, &decimal_digits, &nullable))) return 1;
        for (char *cursor = column_names[index]; *cursor; cursor++) *cursor = (char)tolower((unsigned char)*cursor);
    }
    return 0;
}
int bank_db_fetch(void) {
    SQLRETURN code = SQLFetch(statement);
    if (code == SQL_NO_DATA) return 2;
    if (!successful(code)) return 1;
    for (int index = 0; index < column_count; index++) {
        SQLLEN length;
        memset(columns[index], 0, FIELD_SIZE);
        code = SQLGetData(statement, (SQLUSMALLINT)(index + 1), SQL_C_CHAR, columns[index], FIELD_SIZE, &length);
        if (!successful(code) || length >= FIELD_SIZE || length == SQL_NO_TOTAL) return 1;
        null_columns[index] = length == SQL_NULL_DATA;
    }
    return 0;
}
int bank_db_column(int index, char *output, int size) {
    if (index < 1 || index > column_count) return 1;
    copy_field(output, (size_t)size, null_columns[index - 1] ? "" : columns[index - 1]);
    return 0;
}
int bank_db_rows(void) {
    SQLLEN count;
    if (!successful(SQLRowCount(statement, &count))) return -1;
    return (int)count;
}
int bank_result_row(const char *key) {
    json_object *row = json_object_new_object();
    for (int index = 0; index < column_count; index++) {
        json_object *value = NULL;
        if (!null_columns[index]) {
            if (column_types[index] == SQL_BIGINT || column_types[index] == SQL_INTEGER || column_types[index] == SQL_SMALLINT)
                value = json_object_new_int64(strtoll(columns[index], NULL, 10));
            else value = json_object_new_string(columns[index]);
        }
        json_object_object_add(row, column_names[index], value);
    }
    if (*key) json_object_object_add(response, key, row);
    else json_object_array_add(result_rows, row);
    return 0;
}
int bank_db_end(int commit) {
    if (statement) { SQLFreeHandle(SQL_HANDLE_STMT, statement); statement = 0; }
    if (!successful(SQLEndTran(SQL_HANDLE_DBC, connection, commit ? SQL_COMMIT : SQL_ROLLBACK))) {
        bank_db_discard();
        return 1;
    }
    return 0;
}
int bank_db_close(void) {
    if (worker_mode) return 0;
    return bank_db_discard();
}
int bank_db_discard(void) {
    if (statement) { SQLFreeHandle(SQL_HANDLE_STMT, statement); statement = 0; }
    if (connection) { SQLDisconnect(connection); SQLFreeHandle(SQL_HANDLE_DBC, connection); }
    if (environment) SQLFreeHandle(SQL_HANDLE_ENV, environment);
    connection = 0;
    environment = 0;
    return 0;
}

static void hex_encode(const unsigned char *bytes, size_t size, char *output) {
    static const char hex[] = "0123456789abcdef";
    for (size_t index = 0; index < size; index++) {
        output[index * 2] = hex[bytes[index] >> 4];
        output[index * 2 + 1] = hex[bytes[index] & 15];
    }
    output[size * 2] = 0;
}
int bank_random(char *output, int size) {
    unsigned char random[32];
    char encoded[65];
    if (size < 64 || RAND_bytes(random, sizeof(random)) != 1) return 1;
    hex_encode(random, sizeof(random), encoded);
    copy_field(output, (size_t)size, encoded);
    return 0;
}
int bank_sha256(const char *input, int size, char *output) {
    unsigned char digest[32];
    unsigned int length;
    char encoded[65];
    if (EVP_Digest(input, field_length(input, (size_t)size), digest, &length, EVP_sha256(), NULL) != 1) return 1;
    hex_encode(digest, sizeof(digest), encoded);
    memcpy(output, encoded, 64);
    return 0;
}
int bank_password_hash(const char *password, int length, char *output) {
    unsigned char salt_bytes[16], derived[64];
    char salt[33], hash[129], encoded[256];
    if (RAND_bytes(salt_bytes, sizeof(salt_bytes)) != 1) return 1;
    hex_encode(salt_bytes, sizeof(salt_bytes), salt);
    if (EVP_PBE_scrypt(password, (size_t)length, (unsigned char *)salt, strlen(salt),
                       32768, 8, 1, 64 * 1024 * 1024, derived, sizeof(derived)) != 1) return 1;
    hex_encode(derived, sizeof(derived), hash);
    snprintf(encoded, sizeof(encoded), "scrypt:32768:8:1$%s$%s", salt, hash);
    copy_field(output, 256, encoded);
    OPENSSL_cleanse(derived, sizeof(derived));
    return 0;
}
int bank_password_verify(const char *password, int length, const char *stored) {
    char encoded[257], salt[65], expected[129], actual[129];
    unsigned char derived[64];
    trim_field(encoded, stored, 256);
    /* Accept the existing Werkzeug format, with bounded scrypt parameters. */
    if (sscanf(encoded, "scrypt:32768:8:1$%64[^$]$%128[0-9a-f]", salt, expected) != 2 || strlen(expected) != 128) return 1;
    if (EVP_PBE_scrypt(password, (size_t)length, (unsigned char *)salt, strlen(salt),
                       32768, 8, 1, 64 * 1024 * 1024, derived, sizeof(derived)) != 1) return 1;
    hex_encode(derived, sizeof(derived), actual);
    OPENSSL_cleanse(derived, sizeof(derived));
    return CRYPTO_memcmp(expected, actual, 128) != 0;
}
int bank_utf8_length(const char *text, int length) {
    int count = 0;
    for (int index = 0; index < length; index++) if (((unsigned char)text[index] & 0xc0) != 0x80) count++;
    return count;
}
int bank_migration(int version, char *sql, int size) {
    const char *path;
    if (version == 2) path = "/app/db/migrations/002_create_bank.sql";
    else if (version == 3) path = "/app/db/migrations/003_daily_operations.sql";
    else if (version == 4) path = "/app/db/migrations/004_account_approval.sql";
    else if (version == 5) path = "/app/db/migrations/005_account_resolution.sql";
    else if (version == 6) path = "/app/db/migrations/006_notifications.sql";
    else if (version == 7) path = "/app/db/migrations/007_banking_products.sql";
    else return 1;
    if (!migration) migration = fopen(path, "r");
    if (!migration) return 1;
    memset(sql, ' ', (size_t)size);
    int count = 0, character;
    while ((character = fgetc(migration)) != EOF && character != ';') {
        if (!count && isspace((unsigned char)character)) continue;
        if (count >= size) return 1;
        sql[count++] = (char)character;
    }
    if (!count && character == EOF) { fclose(migration); migration = NULL; return 2; }
    return 0;
}
