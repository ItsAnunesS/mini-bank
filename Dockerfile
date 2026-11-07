FROM debian:bookworm-slim AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends gnucobol \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY src/cobol/ ./src/
RUN cobc -x -free -Wall -Wextra -Werror -I src/copybooks \
    -o /build/despesas src/main.cob \
    src/domain/validate-amount.cob src/adapters/db2-repository.cob

RUN cobc -x -free -Wall -Wextra -Werror -I src/copybooks \
    -o /build/bank-rules src/bank-rules.cob src/domain/validate-amount.cob

FROM build AS unit-tests
COPY tests/cobol/ ./tests/
RUN cobc -x -free -Wall -Wextra -Werror -I src/copybooks \
    -o /build/test-amount tests/test-amount.cob src/domain/validate-amount.cob \
    && /build/test-amount

FROM debian:bookworm-slim AS dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends libcob4 python3 python3-venv libxml2 libstdc++6 libjson-c5 libssl3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home app \
    && mkdir /data && chown app:app /data
COPY requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

FROM build AS bank-build
RUN apt-get update \
    && apt-get install -y --no-install-recommends libjson-c-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=dependencies /opt/venv/lib/python3.11/site-packages/clidriver /opt/ibm/clidriver
COPY --from=dependencies /opt/venv/lib/python3.11/site-packages/ibm_db.libs /opt/ibm/ibm_db.libs
COPY src/native/ ./native/
RUN gcc -Wall -Wextra -Werror -O2 -I /opt/ibm/clidriver/include \
    -c native/bank_bridge.c -o /build/bank_bridge.o
RUN cobc -x -free -Wall -Wextra -Werror -I src -I src/copybooks \
    -Q "-Wl,-rpath-link,/opt/ibm/ibm_db.libs" \
    -L /opt/ibm/clidriver/lib -ldb2 -ljson-c -lcrypto \
    -o /build/bank-backend src/bank-backend.cob \
    src/domain/validate-amount.cob /build/bank_bridge.o

RUN cobc -x -free -Wall -Wextra -Werror -I src -I src/copybooks \
    -Q "-Wl,-rpath-link,/opt/ibm/ibm_db.libs" \
    -L /opt/ibm/clidriver/lib -ldb2 -ljson-c -lcrypto \
    -o /build/bank-worker src/bank-worker.cob src/bank-backend.cob \
    src/domain/validate-amount.cob /build/bank_bridge.o

FROM dependencies AS runtime
COPY --from=bank-build /build/bank-backend /usr/local/bin/bank-backend
COPY --from=bank-build /build/bank-worker /usr/local/bin/bank-worker
ENV LD_LIBRARY_PATH=/opt/venv/lib/python3.11/site-packages/clidriver/lib
COPY src/python/ /app/
COPY db/ /app/db/
COPY --from=unit-tests /build/despesas /usr/local/bin/despesas
COPY --from=unit-tests /build/bank-rules /usr/local/bin/bank-rules
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1
USER app
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/despesas"]

FROM runtime AS integration-tests
COPY tests/integration/ /app/tests/
ENTRYPOINT ["/opt/venv/bin/python", "/app/tests/test_db.py"]

FROM runtime AS bank-web
ENTRYPOINT ["/opt/venv/bin/gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "30", "bank.web:create_app()"]

FROM runtime AS bank-tests
COPY tests/bank/ /app/tests/bank/
ENTRYPOINT ["/opt/venv/bin/python", "-m", "unittest", "discover", "-s", "/app/tests/bank", "-v"]
