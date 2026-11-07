"""Persistent native transport boundaries, isolation, failures and recovery."""
import json
import os
import signal
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from bank import client as backend
from bank.client import BankError, BackendError
from bank import transport
from fixtures import IsolatedBankTestCase
from support import execute, insert_id, query, transaction


class WorkerProtocolTests(unittest.TestCase):
    def test_invalid_frames_do_not_corrupt_following_requests(self):
        frames = ['[]', '{} trailing', '{"command":42}', 'x' * 17000,
                  json.dumps({'command': 'SESSION'}), json.dumps({'command': 'SESSION'})]
        result = subprocess.run(['/usr/local/bin/bank-worker'], input='\n'.join(frames)+'\n',
                                text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 0)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(responses), len(frames))
        self.assertEqual(responses[:4], [{'ok': False, 'error': 'INVALID_REQUEST'}] * 4)
        self.assertTrue(all(r['ok'] and r['rows'] == [] for r in responses[4:]))


class PersistentWorkerTests(IsolatedBankTestCase):
    def setUp(self):
        with transaction() as c:
            for table in self.tables:
                execute(c, f'DELETE FROM {table}')
            self.admin_id = insert_id(c, 'INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?)',
                                     ('Worker admin', 'admin@example.test', self.hashed, 'ADMIN'))
            self.owner_id = insert_id(c, 'INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?)',
                                     ('Worker owner', 'owner@example.test', self.hashed, 'CUSTOMER'))
        self.admin = backend.authenticate('admin@example.test', self.password)
        self.owner = backend.authenticate('owner@example.test', self.password)
        self.account = backend.create_account(self.owner)
        backend.approve_account(self.admin, self.account)

    def test_reuse_resets_authority_results_errors_and_request_fields(self):
        worker = transport._local.worker
        self.assertTrue(backend.list_accounts(self.admin))
        with self.assertRaises(BankError) as error:
            backend.call_backend('ACCOUNTS')
        self.assertEqual(error.exception.code, 'UNAUTHENTICATED')
        with self.assertRaises(BankError):
            backend.create_customer(self.owner, 'Forbidden', 'forbidden@example.test', self.password)
        response = backend.call_backend('SESSION')
        self.assertEqual(response['rows'], [])
        self.assertNotIn('account', response)
        self.assertNotIn('token', response)
        self.assertNotIn('error', response)
        backend.move_money(self.admin, None, self.account, '1', str(uuid4()))
        with self.assertRaises(BankError):
            backend.call_backend('MOVE', token=self.admin, target=self.account, request_key=str(uuid4()))
        self.assertEqual(backend.statement(self.owner, self.account)[0]['balance'], 1)
        backend.revoke_session(self.owner)
        self.assertIsNone(backend.session_user(self.owner))
        self.assertIs(transport._local.worker, worker)

    def test_concurrent_threads_have_independent_connections_and_authority(self):
        def attempt(token):
            try:
                return backend.create_account(token, 'owner@example.test'), transport._local.worker.process.pid
            finally:
                backend.close_backend()
        # Both calls are valid but run under separate identities and connections.
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (self.admin, self.owner)))
        self.assertEqual(len({pid for _, pid in results}), 2)
        self.assertEqual(len({account for account, _ in results}), 2)
        self.assertEqual(len(backend.list_accounts(self.owner)), 3)

    def test_database_failure_rolls_back_and_next_request_recovers(self):
        with transaction() as c:
            execute(c, "CREATE TRIGGER TEST_WORKER_FAILURE NO CASCADE BEFORE INSERT ON BANK_ENTRIES FOR EACH ROW SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'Test failure'")
        try:
            with self.assertRaises(BackendError):
                backend.move_money(self.admin, None, self.account, '10', str(uuid4()))
        finally:
            with transaction() as c:
                execute(c, 'DROP TRIGGER TEST_WORKER_FAILURE')
        self.assertEqual(backend.statement(self.owner, self.account)[0]['balance'], 0)
        backend.move_money(self.admin, None, self.account, '2', str(uuid4()))
        self.assertEqual(backend.statement(self.owner, self.account)[0]['balance'], 2)

    def test_failed_worker_is_not_replayed_and_next_request_recovers(self):
        worker = transport._local.worker
        process = worker.process
        os.kill(process.pid, signal.SIGSTOP)
        # A stopped worker cannot consume the request; the transport must time out once.
        with self.assertRaises(TimeoutError):
            worker.exchange({'command': 'SESSION', 'token': self.owner}, timeout=0.05)
        self.assertIsNone(worker.process)
        self.assertIsNotNone(process.poll())
        self.assertEqual(backend.session_user(self.owner)['id'], self.owner_id)
        self.assertIsNot(transport._local.worker, worker)

    def test_environment_change_replaces_connection_instead_of_reusing_old_schema(self):
        worker = transport._local.worker
        with patch.dict(os.environ, {'DB2_SCHEMA': 'INVALID;SCHEMA'}):
            with self.assertRaises(BackendError):
                backend.list_accounts(self.owner)
        self.assertIsNone(worker.process)
        self.assertEqual(backend.session_user(self.owner)['id'], self.owner_id)

    def test_mutations_are_visible_to_an_independent_connection_after_response(self):
        backend.move_money(self.admin, None, self.account, '3', str(uuid4()))
        with transaction() as c:
            self.assertEqual(query(c, 'SELECT BALANCE FROM BANK_ACCOUNTS WHERE ID = ?', (self.account,))[0]['balance'], 3)
        backend.set_account_status(self.admin, self.account, 'BLOCKED')
        with self.assertRaises(BankError):
            backend.move_money(self.admin, None, self.account, '1', str(uuid4()))
        with transaction() as c:
            self.assertEqual(query(c, 'SELECT BALANCE FROM BANK_ACCOUNTS WHERE ID = ?', (self.account,))[0]['balance'], 3)

    def test_forked_process_does_not_reuse_or_terminate_parent_worker(self):
        parent_worker = transport._local.worker
        parent_native_pid = parent_worker.process.pid
        read_fd, write_fd = os.pipe()
        child_pid = os.fork()
        if child_pid == 0:
            os.close(read_fd)
            try:
                user = backend.session_user(self.owner)
                result = {'user_id': user['id'], 'native_pid': transport._local.worker.process.pid}
                os.write(write_fd, json.dumps(result).encode())
                backend.close_backend()
                os._exit(0)
            except Exception:
                os._exit(1)
        os.close(write_fd)
        try:
            payload = os.read(read_fd, 4096)
        finally:
            os.close(read_fd)
        _, status = os.waitpid(child_pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        result = json.loads(payload)
        self.assertEqual(result['user_id'], self.owner_id)
        self.assertNotEqual(result['native_pid'], parent_native_pid)
        self.assertIsNone(parent_worker.process.poll())
        self.assertEqual(backend.session_user(self.owner)['id'], self.owner_id)
