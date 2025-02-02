import datetime
import getpass
import json
import logging
import os
import re
import tempfile
import threading
import unittest
import uuid
from functools import partial
import pytest

from tests.waiter import util, cli


@pytest.mark.cli
@pytest.mark.timeout(util.DEFAULT_TEST_TIMEOUT_SECS)
class WaiterCliTest(util.WaiterTest):

    @classmethod
    def setUpClass(cls):
        cls.waiter_url = util.retrieve_waiter_url()
        util.init_waiter_session(cls.waiter_url)
        cli.write_base_config()

    def setUp(self):
        self.waiter_url = type(self).waiter_url
        self.logger = logging.getLogger(__name__)

    def test_basic_create(self):
        token_name = self.token_name()
        version = str(uuid.uuid4())
        cmd = util.minimal_service_cmd()
        cp = cli.create_minimal(self.waiter_url, token_name, flags=None, cmd=cmd, cpus=0.1, mem=128, version=version)
        self.assertEqual(0, cp.returncode, cp.stderr)
        try:
            self.assertIn('Attempting to create', cli.stdout(cp))
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertIsNotNone(token_data)
            self.assertEqual('shell', token_data['cmd-type'])
            self.assertEqual(cmd, token_data['cmd'])
            self.assertEqual(0.1, token_data['cpus'])
            self.assertEqual(128, token_data['mem'])
            self.assertEqual(getpass.getuser(), token_data['owner'])
            self.assertEqual(getpass.getuser(), token_data['last-update-user'])
            self.assertEqual({}, token_data['previous'])
            self.assertEqual(version, token_data['version'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_basic_update(self):
        token_name = self.token_name()
        version = str(uuid.uuid4())
        cmd = util.minimal_service_cmd()
        cp = cli.update_minimal(self.waiter_url, token_name, flags=None, cmd=cmd, cpus=0.1, mem=128, version=version)
        self.assertEqual(0, cp.returncode, cp.stderr)
        try:
            self.assertIn('Attempting to update', cli.stdout(cp))
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertIsNotNone(token_data)
            self.assertEqual('shell', token_data['cmd-type'])
            self.assertEqual(cmd, token_data['cmd'])
            self.assertEqual(0.1, token_data['cpus'])
            self.assertEqual(128, token_data['mem'])
            self.assertEqual(getpass.getuser(), token_data['owner'])
            self.assertEqual(getpass.getuser(), token_data['last-update-user'])
            self.assertEqual({}, token_data['previous'])
            self.assertEqual(version, token_data['version'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_failed_create(self):
        service = util.minimal_service_description(cpus=0)
        cp = cli.create_from_service_description(self.waiter_url, self.token_name(), service)
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('Service description', cli.decode(cp.stderr))
        self.assertIn('improper', cli.decode(cp.stderr))
        self.assertIn('cpus must be a positive number', cli.decode(cp.stderr))

    def __test_no_cluster(self, cli_fn):
        config = {'clusters': []}
        with cli.temp_config_file(config) as path:
            flags = '--config %s' % path
            cp = cli_fn(token_name=self.token_name(), flags=flags)
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('must specify at least one cluster', cli.decode(cp.stderr))

    def test_create_no_cluster(self):
        self.__test_no_cluster(cli.create_minimal)

    def test_unspecified_create_cluster(self):
        config = {
            'clusters': [
                {"name": "Foo", "url": self.waiter_url},
                {"name": "Bar", "url": self.waiter_url}
            ]
        }
        with cli.temp_config_file(config) as path:
            flags = '--config %s' % path
            cp = cli.create_minimal(token_name=self.token_name(), flags=flags)
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('must either specify a cluster via --cluster or set "default-for-create" to true',
                          cli.decode(cp.stderr))

    def test_over_specified_create_cluster(self):
        config = {
            'clusters': [
                {"name": "Foo", "url": self.waiter_url, "default-for-create": True},
                {"name": "Bar", "url": self.waiter_url, "default-for-create": True}
            ]
        }
        with cli.temp_config_file(config) as path:
            flags = '--config %s' % path
            cp = cli.create_minimal(token_name=self.token_name(), flags=flags)
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('have "default-for-create" set to true for more than one cluster', cli.decode(cp.stderr))

    def test_single_specified_create_cluster(self):
        config = {
            'clusters': [
                {"name": "Foo", "url": str(uuid.uuid4())},
                {"name": "Bar", "url": self.waiter_url, "default-for-create": True}
            ]
        }
        with cli.temp_config_file(config) as path:
            token_name = self.token_name()
            flags = '--config %s' % path
            cp = cli.create_minimal(token_name=token_name, flags=flags)
            self.assertEqual(0, cp.returncode, cp.stderr)
            try:
                token = util.load_token(self.waiter_url, token_name)
                self.assertIsNotNone(token)
            finally:
                util.delete_token(self.waiter_url, token_name)

    def test_create_single_cluster(self):
        config = {'clusters': [{"name": "Bar", "url": self.waiter_url}]}
        with cli.temp_config_file(config) as path:
            token_name = self.token_name()
            flags = '--config %s' % path
            cp = cli.create_minimal(token_name=token_name, flags=flags)
            self.assertEqual(0, cp.returncode, cp.stderr)
            try:
                token = util.load_token(self.waiter_url, token_name)
                self.assertIsNotNone(token)
            finally:
                util.delete_token(self.waiter_url, token_name)

    def test_implicit_create_args(self):
        cp = cli.create(create_flags='--help')
        self.assertEqual(0, cp.returncode, cp.stderr)
        self.assertIn('--cpus', cli.stdout(cp))
        self.assertNotIn('--https-redirect', cli.stdout(cp))
        self.assertNotIn('--fallback-period-secs', cli.stdout(cp))
        self.assertNotIn('--idle-timeout-mins', cli.stdout(cp))
        self.assertNotIn('--max-instances', cli.stdout(cp))
        self.assertNotIn('--restart-backoff-factor', cli.stdout(cp))
        self.assertNotIn('--health-check-port-index', cli.stdout(cp))
        self.assertNotIn('--concurrency-level', cli.stdout(cp))
        self.assertNotIn('--health-check-max-consecutive-failures', cli.stdout(cp))
        self.assertNotIn('--max-queue-length', cli.stdout(cp))
        self.assertNotIn('--expired-instance-restart-rate', cli.stdout(cp))
        self.assertNotIn('--jitter-threshold', cli.stdout(cp))
        token_name = self.token_name()
        cp = cli.create(self.waiter_url, token_name, create_flags=('--https-redirect true '
                                                                   '--cpus 0.1 '
                                                                   '--fallback-period-secs 10 '
                                                                   '--idle-timeout-mins 1 '
                                                                   '--max-instances 100 '
                                                                   '--restart-backoff-factor 1.1 '
                                                                   '--health-check-port-index 1 '
                                                                   '--concurrency-level 1000 '
                                                                   '--health-check-max-consecutive-failures 10 '
                                                                   '--max-queue-length 1000000 '
                                                                   '--expired-instance-restart-rate 0.1 '
                                                                   '--jitter-threshold 0.1 '))
        self.assertEqual(0, cp.returncode, cp.stderr)
        try:
            token = util.load_token(self.waiter_url, token_name)
            self.assertTrue(token['https-redirect'])
            self.assertEqual(10, token['fallback-period-secs'])
            self.assertEqual(1, token['idle-timeout-mins'])
            self.assertEqual(100, token['max-instances'])
            self.assertEqual(1.1, token['restart-backoff-factor'])
            self.assertEqual(1, token['health-check-port-index'])
            self.assertEqual(1000, token['concurrency-level'])
            self.assertEqual(10, token['health-check-max-consecutive-failures'])
            self.assertEqual(1000000, token['max-queue-length'])
            self.assertEqual(0.1, token['expired-instance-restart-rate'])
            self.assertEqual(0.1, token['jitter-threshold'])
            cp = cli.create(self.waiter_url, token_name, create_flags=('--https-redirect false '
                                                                       '--cpus 0.1 '
                                                                       '--fallback-period-secs 20 '
                                                                       '--idle-timeout-mins 2 '
                                                                       '--max-instances 200 '
                                                                       '--restart-backoff-factor 2.2 '
                                                                       '--health-check-port-index 2 '
                                                                       '--concurrency-level 2000 '
                                                                       '--health-check-max-consecutive-failures 2 '
                                                                       '--max-queue-length 2000000 '
                                                                       '--expired-instance-restart-rate 0.2 '
                                                                       '--jitter-threshold 0.2 '))
            self.assertEqual(0, cp.returncode, cp.stderr)
            token = util.load_token(self.waiter_url, token_name)
            self.assertFalse(token['https-redirect'])
            self.assertEqual(20, token['fallback-period-secs'])
            self.assertEqual(2, token['idle-timeout-mins'])
            self.assertEqual(200, token['max-instances'])
            self.assertEqual(2.2, token['restart-backoff-factor'])
            self.assertEqual(2, token['health-check-port-index'])
            self.assertEqual(2000, token['concurrency-level'])
            self.assertEqual(2, token['health-check-max-consecutive-failures'])
            self.assertEqual(2000000, token['max-queue-length'])
            self.assertEqual(0.2, token['expired-instance-restart-rate'])
            self.assertEqual(0.2, token['jitter-threshold'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_create_help_text(self):
        cp = cli.create(create_flags='--help')
        self.assertEqual(0, cp.returncode, cp.stderr)
        self.assertIn('memory (in MiB) to reserve', cli.stdout(cp))

    def test_cli_invalid_file_format_combo(self):
        cp = cli.create(self.waiter_url, create_flags='--json test.json --yaml test.yaml')
        self.assertEqual(2, cp.returncode, cp.stderr)
        self.assertIn('not allowed with argument', cli.stderr(cp))

        cp = cli.update(self.waiter_url, update_flags='--json test.json --yaml test.yaml')
        self.assertEqual(2, cp.returncode, cp.stderr)
        self.assertIn('not allowed with argument', cli.stderr(cp))

        token_name = self.token_name()
        cp = cli.show(self.waiter_url, token_name, show_flags='--json --yaml')
        self.assertEqual(2, cp.returncode, cp.stderr)
        self.assertIn('not allowed with argument', cli.stderr(cp))

        token_name = self.token_name()
        cp = cli.show(self.waiter_url, token_name, show_flags='--json --yaml')
        self.assertEqual(2, cp.returncode, cp.stderr)
        self.assertIn('not allowed with argument', cli.stderr(cp))

        cp = cli.tokens(self.waiter_url, tokens_flags='--json --yaml')
        self.assertEqual(2, cp.returncode, cp.stderr)
        self.assertIn('not allowed with argument', cli.stderr(cp))

    def test_implicit_update_args(self):
        cp = cli.create(create_flags='--help')
        self.assertEqual(0, cp.returncode, cp.stderr)
        self.assertIn('--cpus', cli.stdout(cp))
        self.assertNotIn('--https-redirect', cli.stdout(cp))
        self.assertNotIn('--fallback-period-secs', cli.stdout(cp))
        self.assertNotIn('--idle-timeout-mins', cli.stdout(cp))
        self.assertNotIn('--max-instances', cli.stdout(cp))
        self.assertNotIn('--restart-backoff-factor', cli.stdout(cp))
        token_name = self.token_name()
        cp = cli.update(self.waiter_url, token_name, update_flags='--https-redirect true '
                                                                  '--cpus 0.1 '
                                                                  '--fallback-period-secs 10 '
                                                                  '--idle-timeout-mins 1 '
                                                                  '--max-instances 100 '
                                                                  '--restart-backoff-factor 1.1')
        self.assertEqual(0, cp.returncode, cp.stderr)
        try:
            token = util.load_token(self.waiter_url, token_name)
            self.assertTrue(token['https-redirect'])
            self.assertEqual(10, token['fallback-period-secs'])
            self.assertEqual(1, token['idle-timeout-mins'])
            self.assertEqual(100, token['max-instances'])
            self.assertEqual(1.1, token['restart-backoff-factor'])
            cp = cli.update(self.waiter_url, token_name, update_flags='--https-redirect false '
                                                                      '--cpus 0.1 '
                                                                      '--fallback-period-secs 20 '
                                                                      '--idle-timeout-mins 2 '
                                                                      '--max-instances 200 '
                                                                      '--restart-backoff-factor 2.2')
            self.assertEqual(0, cp.returncode, cp.stderr)
            token = util.load_token(self.waiter_url, token_name)
            self.assertFalse(token['https-redirect'])
            self.assertEqual(20, token['fallback-period-secs'])
            self.assertEqual(2, token['idle-timeout-mins'])
            self.assertEqual(200, token['max-instances'])
            self.assertEqual(2.2, token['restart-backoff-factor'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_basic_show(self):
        token_name = self.token_name()
        cp = cli.show(self.waiter_url, token_name)
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('No matching data found', cli.stdout(cp))
        token_definition = {
            'cmd-type': 'shell',
            'health-check-url': '/foo',
            'min-instances': 1,
            'max-instances': 2,
            'permitted-user': '*',
            'mem': 1024
        }
        util.post_token(self.waiter_url, token_name, token_definition)
        try:
            token = util.load_token(self.waiter_url, token_name)
            self.assertIsNotNone(token)
            cp = cli.show(self.waiter_url, token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Command type', cli.stdout(cp))
            self.assertIn('Health check endpoint', cli.stdout(cp))
            self.assertIn('Minimum instances', cli.stdout(cp))
            self.assertIn('Maximum instances', cli.stdout(cp))
            self.assertIn('Permitted user(s)', cli.stdout(cp))
            self.assertIn(f'=== {self.waiter_url} / {token_name} ===', cli.stdout(cp))
            self.assertIn('1 GiB', cli.stdout(cp))
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_implicit_show_fields(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1, 'https-redirect': True, 'fallback-period-secs': 10})
        try:
            cp = cli.show(self.waiter_url, token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Https redirect', cli.stdout(cp))
            self.assertIn('Fallback period (seconds)', cli.stdout(cp))
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_show_no_cluster(self):
        config = {'clusters': []}
        with cli.temp_config_file(config) as path:
            flags = '--config %s' % path
            cp = cli.show(token_name=self.token_name(), flags=flags)
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('must specify at least one cluster', cli.decode(cp.stderr))

    def __test_show(self, file_format):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1})
        try:
            cp, tokens = cli.show_token(file_format, self.waiter_url, token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertEqual(1, len(tokens))
            self.assertEqual(util.load_token(self.waiter_url, token_name), tokens[0])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_show_json(self):
        self.__test_show('json')

    def test_show_yaml(self):
        self.__test_show('yaml')

    @pytest.mark.serial
    def test_create_if_match(self):

        def encountered_stale_token_error(cp):
            self.logger.info(f'Return code: {cp.returncode}, output: {cli.output(cp)}')
            assert 1 == cp.returncode
            assert 'stale token' in cli.decode(cp.stderr)
            return True

        token_name = self.token_name()
        keep_running = True

        def update_token_loop():
            mem = 1
            while keep_running:
                util.post_token(self.waiter_url, token_name, {'mem': mem}, assert_response=False)
                mem += 1

        util.post_token(self.waiter_url, token_name, {'cpus': 0.1})
        thread = threading.Thread(target=update_token_loop)
        try:
            thread.start()
            util.wait_until(lambda: cli.create_minimal(self.waiter_url, token_name),
                            encountered_stale_token_error,
                            wait_interval_ms=0)
        finally:
            keep_running = False
            thread.join()
            self.logger.info('Thread finished')
            util.delete_token(self.waiter_url, token_name)

    @unittest.skipIf('WAITER_TEST_CLI_COMMAND' in os.environ, 'waiter executable may be unknown.')
    def test_base_config_file(self):
        token_name = self.token_name()
        cluster_name_1 = str(uuid.uuid4())
        config = {'clusters': [{"name": cluster_name_1, "url": self.waiter_url}]}
        with cli.temp_base_config_file(config):
            # Use entry in base config file
            cp = cli.create_minimal(token_name=token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)
            try:
                self.assertIn(f'on {cluster_name_1}', cli.decode(cp.stdout))

                # Overwrite "base" with specified config file
                cluster_name_2 = str(uuid.uuid4())
                config = {'clusters': [{"name": cluster_name_2, "url": self.waiter_url}]}
                with cli.temp_config_file(config) as path:
                    # Verify "base" config is overwritten
                    flags = '--config %s' % path
                    cp = cli.create_minimal(token_name=token_name, flags=flags)
                    self.assertEqual(0, cp.returncode, cp.stderr)
                    self.assertIn(f'on {cluster_name_2}', cli.decode(cp.stdout))
            finally:
                util.delete_token(self.waiter_url, token_name)

    def test_avoid_exit_on_connection_error(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1})
        try:
            config = {'clusters': [{'name': 'foo', 'url': self.waiter_url},
                                   {'name': 'bar', 'url': 'http://localhost:65535'}]}
            with cli.temp_config_file(config) as path:
                flags = f'--config {path}'
                cp, tokens = cli.show_token('json', token_name=token_name, flags=flags)
                self.assertEqual(0, cp.returncode, cp.stderr)
                self.assertEqual(1, len(tokens), tokens)
                self.assertEqual(util.load_token(self.waiter_url, token_name), tokens[0])
                self.assertIn('Encountered connection error with bar', cli.decode(cp.stderr), cli.output(cp))
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_show_env(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'env': {'FOO': '1', 'BAR': 'baz'}})
        try:
            cp = cli.show(self.waiter_url, token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Environment:\n', cli.stdout(cp))
            self.assertNotIn('Env ', cli.stdout(cp))
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_delete_basic(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1})
        try:
            cp = cli.delete(self.waiter_url, token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Deleting token', cli.stdout(cp))
            self.assertIn('Successfully deleted', cli.stdout(cp))
            resp_json = util.load_token(self.waiter_url, token_name, expected_status_code=404)
            self.assertIn('waiter-error', resp_json)
        finally:
            util.delete_token(self.waiter_url, token_name, assert_response=False)

    def test_delete_single_service(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            self.logger.info(f'Token: {util.load_token(self.waiter_url, token_name)}')
            service_id = util.ping_token(self.waiter_url, token_name)
            try:
                cp = cli.delete(self.waiter_url, token_name)
                self.assertEqual(1, cp.returncode, cli.output(cp))
                self.assertIn('There is one service using token', cli.stderr(cp))
                self.assertIn('Please kill this service before deleting the token', cli.stderr(cp))
                self.assertIn(service_id, cli.stderr(cp))
            finally:
                util.kill_service(self.waiter_url, service_id)
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_delete_multiple_services(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            self.logger.info(f'Token: {util.load_token(self.waiter_url, token_name)}')
            service_id_1 = util.ping_token(self.waiter_url, token_name)
            try:
                util.post_token(self.waiter_url, token_name, util.minimal_service_description())
                self.logger.info(f'Token: {util.load_token(self.waiter_url, token_name)}')
                service_id_2 = util.ping_token(self.waiter_url, token_name)
                try:
                    services_for_token = util.services_for_token(self.waiter_url, token_name)
                    self.logger.info(f'Services for token {token_name}: {json.dumps(services_for_token, indent=2)}')
                    cp = cli.delete(self.waiter_url, token_name)
                    self.assertEqual(1, cp.returncode, cli.output(cp))
                    self.assertIn('There are 2 services using token', cli.stderr(cp))
                    self.assertIn('Please kill these services before deleting the token', cli.stderr(cp))
                    self.assertIn(service_id_1, cli.stderr(cp))
                    self.assertIn(service_id_2, cli.stderr(cp))
                finally:
                    util.kill_service(self.waiter_url, service_id_2)
            finally:
                util.kill_service(self.waiter_url, service_id_1)
        finally:
            util.delete_token(self.waiter_url, token_name)

    @pytest.mark.serial
    def test_delete_if_match(self):

        def encountered_stale_token_error(cp):
            self.logger.info(f'Return code: {cp.returncode}, output: {cli.output(cp)}')
            assert 1 == cp.returncode
            assert 'stale token' in cli.decode(cp.stderr)
            return True

        token_name = self.token_name()
        keep_running = True

        def update_token_loop():
            mem = 1
            while keep_running:
                util.post_token(self.waiter_url, token_name, {'mem': mem}, assert_response=False)
                mem += 1

        util.post_token(self.waiter_url, token_name, {'cpus': 0.1})
        thread = threading.Thread(target=update_token_loop)
        try:
            thread.start()
            util.wait_until(lambda: cli.delete(self.waiter_url, token_name),
                            encountered_stale_token_error,
                            wait_interval_ms=0)
        finally:
            keep_running = False
            thread.join()
            self.logger.info('Thread finished')
            util.delete_token(self.waiter_url, token_name, assert_response=False)

    def test_delete_non_existent_token(self):
        token_name = self.token_name()
        cp = cli.delete(self.waiter_url, token_name)
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('No matching data found', cli.stdout(cp))

    def test_ping_basic(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            self.assertEqual(0, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli.ping(self.waiter_url, token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Pinging token', cli.stdout(cp))
            self.assertIn('successful', cli.stdout(cp))
            self.assertIn('Service is currently', cli.stdout(cp))
            self.assertTrue(any(s in cli.stdout(cp) for s in ['Running', 'Starting']))
            util.wait_until_services_for_token(self.waiter_url, token_name, 1)
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_ping_error(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1})
        try:
            self.assertEqual(0, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli.ping(self.waiter_url, token_name)
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('Pinging token', cli.stdout(cp))
            self.assertEqual(0, len(util.services_for_token(self.waiter_url, token_name)))
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_ping_non_existent_token(self):
        token_name = self.token_name()
        cp = cli.ping(self.waiter_url, token_name)
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('No matching data found', cli.stdout(cp))

    def test_ping_custom_health_check_endpoint(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description(**{'health-check-url': '/sleep'}))
        try:
            self.assertEqual(0, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli.ping(self.waiter_url, token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Pinging token', cli.stdout(cp))
            self.assertEqual(1, len(util.services_for_token(self.waiter_url, token_name)))
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_kill_basic(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            service_id = util.ping_token(self.waiter_url, token_name)
            self.assertEqual(1, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli.kill(self.waiter_url, token_name, flags="-v")
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Killing service', cli.stdout(cp))
            self.assertIn(service_id, cli.stdout(cp))
            self.assertIn('Successfully killed', cli.stdout(cp))
            self.assertIn('timeout=30000', cli.stderr(cp))
            util.wait_until_no_services_for_token(self.waiter_url, token_name)
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_kill_no_services(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            cp = cli.kill(self.waiter_url, token_name)
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('There are no services using token', cli.stdout(cp))
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_kill_timeout(self):
        timeout = 10
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            service_id = util.ping_token(self.waiter_url, token_name)
            self.assertEqual(1, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli.kill(self.waiter_url, token_name, flags="-v", kill_flags=f"--timeout {timeout}")
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Killing service', cli.stdout(cp))
            self.assertIn(service_id, cli.stdout(cp))
            self.assertIn('Successfully killed', cli.stdout(cp))
            self.assertIn(f'timeout={timeout * 1000}', cli.stderr(cp))
            util.wait_until_no_services_for_token(self.waiter_url, token_name)
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_kill_multiple_services(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            service_id_1 = util.ping_token(self.waiter_url, token_name)
            util.post_token(self.waiter_url, token_name, util.minimal_service_description())
            service_id_2 = util.ping_token(self.waiter_url, token_name)
            self.assertEqual(2, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli.kill(self.waiter_url, token_name, kill_flags='--force')
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('There are 2 services using token', cli.stdout(cp))
            self.assertEqual(2, cli.stdout(cp).count('Killing service'))
            self.assertEqual(2, cli.stdout(cp).count('Successfully killed'))
            self.assertIn(service_id_1, cli.stdout(cp))
            self.assertIn(service_id_2, cli.stdout(cp))
            util.wait_until_no_services_for_token(self.waiter_url, token_name)
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    @pytest.mark.xfail
    def test_kill_services_sorted(self):
        token_name = self.token_name()
        service_description_1 = util.minimal_service_description()
        util.post_token(self.waiter_url, token_name, service_description_1)
        try:
            # Create two services for the token
            service_id_1 = util.ping_token(self.waiter_url, token_name)
            service_description_2 = util.minimal_service_description()
            util.post_token(self.waiter_url, token_name, service_description_2)
            service_id_2 = util.ping_token(self.waiter_url, token_name)

            # Kill the two services and assert the sort order
            cp = cli.kill(self.waiter_url, token_name, kill_flags='--force')
            stdout = cli.stdout(cp)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn(service_id_1, stdout)
            self.assertIn(service_id_2, stdout)
            self.assertLess(stdout.index(service_id_2), stdout.index(service_id_1))
            util.wait_until_routers_recognize_service_killed(self.waiter_url, service_id_1)
            util.wait_until_routers_recognize_service_killed(self.waiter_url, service_id_2)

            # Re-create the same two services, in the opposite order
            util.post_token(self.waiter_url, token_name, service_description_2)
            util.ping_token(self.waiter_url, token_name)
            util.post_token(self.waiter_url, token_name, service_description_1)
            util.ping_token(self.waiter_url, token_name)

            # Kill the two services and assert the (different) sort order
            cp = cli.kill(self.waiter_url, token_name, kill_flags='--force')
            stdout = cli.stdout(cp)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn(service_id_1, stdout)
            self.assertIn(service_id_2, stdout)
            self.assertLess(stdout.index(service_id_1), stdout.index(service_id_2))
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_ping_timeout(self):
        token_name = self.token_name()
        command = f'{util.default_cmd()} --start-up-sleep-ms 20000'
        util.post_token(self.waiter_url, token_name, util.minimal_service_description(cmd=command))
        try:
            cp = cli.ping(self.waiter_url, token_name, ping_flags='--timeout 300')
            self.assertEqual(0, cp.returncode, cp.stderr)
            util.kill_services_using_token(self.waiter_url, token_name)
            cp = cli.ping(self.waiter_url, token_name, ping_flags='--timeout 10')
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertTrue(
                # Either Waiter will inform us that the ping timed out
                'Ping request timed out' in cli.stderr(cp) or
                # Or, the read from Waiter will time out
                'Encountered error while pinging' in cli.stderr(cp))
        finally:
            util.kill_services_using_token(self.waiter_url, token_name)
            util.delete_token(self.waiter_url, token_name)

    def test_ping_service_id(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            service_id = util.ping_token(self.waiter_url, token_name)
            util.kill_services_using_token(self.waiter_url, token_name)
            self.assertEqual(0, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli.ping(self.waiter_url, service_id, ping_flags='--service-id')
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Pinging service', cli.stdout(cp))
            self.assertEqual(1, len(util.services_for_token(self.waiter_url, token_name)))
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_ping_invalid_args(self):
        cp = cli.ping(self.waiter_url)
        self.assertEqual(2, cp.returncode, cp.stderr)
        self.assertIn('the following arguments are required: token-or-service-id', cli.stderr(cp))

    def test_ping_correct_endpoint(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name,
                        util.minimal_service_description(**{'health-check-url': '/sleep'}))
        try:
            # Grab the service id for the /sleep version
            service_id = util.ping_token(self.waiter_url, token_name)

            # Update the health check url to /status
            util.post_token(self.waiter_url, token_name,
                            util.minimal_service_description(**{'health-check-url': '/status'}))

            # Pinging the token should use /status
            cp = cli.ping(self.waiter_url, token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)

            # Pinging the service id should use /sleep
            cp = cli.ping(self.waiter_url, service_id, ping_flags='--service-id')
            self.assertEqual(0, cp.returncode, cp.stderr)
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_ping_no_wait(self):
        token_name = self.token_name()
        command = f'{util.default_cmd()} --start-up-sleep-ms {util.DEFAULT_TEST_TIMEOUT_SECS * 2 * 1000}'
        util.post_token(self.waiter_url, token_name, util.minimal_service_description(cmd=command))
        try:
            cp = cli.ping(self.waiter_url, token_name, ping_flags='--no-wait')
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Service is currently Starting', cli.stdout(cp))
            services_for_token = util.wait_until_services_for_token(self.waiter_url, token_name, 1)

            service_id = services_for_token[0]['service-id']
            util.kill_services_using_token(self.waiter_url, token_name)
            cp = cli.ping(self.waiter_url, service_id, ping_flags='--service-id --no-wait')
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Service is currently Starting', cli.stdout(cp))
            util.wait_until_services_for_token(self.waiter_url, token_name, 1)

            util.kill_services_using_token(self.waiter_url, token_name)
            util.post_token(self.waiter_url, token_name, {'cpus': 0.1, 'cmd-type': 'shell'})
            cp = cli.ping(self.waiter_url, token_name, ping_flags='--no-wait')
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertNotIn('Service is currently', cli.stdout(cp))
            self.assertIn('Service description', cli.decode(cp.stderr))
            self.assertIn('improper', cli.decode(cp.stderr))
            self.assertIn('cmd must be a non-empty string', cli.decode(cp.stderr))
            self.assertIn('version must be a non-empty string', cli.decode(cp.stderr))
            self.assertIn('mem must be a positive number', cli.decode(cp.stderr))
            util.wait_until_no_services_for_token(self.waiter_url, token_name)
        finally:
            util.kill_services_using_token(self.waiter_url, token_name)
            util.delete_token(self.waiter_url, token_name)

    def test_ping_deployment_errors(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description(**{'cmd': 'asdfasdfafsdhINVALIDCOMMAND'}))
        try:
            self.assertEqual(0, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli.ping(self.waiter_url, token_name)
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('Pinging token', cli.stdout(cp))
            self.assertIn('Ping responded with non-200 status 503.', cli.stderr(cp))
            self.assertIn('Deployment error: Invalid startup command', cli.stderr(cp))
            self.assertEqual(1, len(util.services_for_token(self.waiter_url, token_name)))
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_create_does_not_patch(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1})
        try:
            cp = cli.create_from_service_description(self.waiter_url, token_name, {'mem': 128})
            self.assertEqual(0, cp.returncode, cp.stderr)
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertFalse('cpus' in token_data)
            self.assertEqual(128, token_data['mem'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_update_does_patch(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1})
        try:
            cp = cli.update_from_service_description(self.waiter_url, token_name, {'mem': 128})
            self.assertEqual(0, cp.returncode, cp.stderr)
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual(0.1, token_data['cpus'])
            self.assertEqual(128, token_data['mem'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def __test_create_token(self, file_format, input_flag=None):
        if input_flag is None:
            input_flag = file_format

        create_fields = {'cpus': 0.1, 'mem': 128}
        stdin = cli.dump(file_format, create_fields)

        token_name = self.token_name()
        cp = cli.create(self.waiter_url, token_name, create_flags=f'--{input_flag} -', stdin=stdin)
        self.assertEqual(0, cp.returncode, cp.stderr)
        try:
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual(0.1, token_data['cpus'])
            self.assertEqual(128, token_data['mem'])

            # Test with data from a file
            util.delete_token(self.waiter_url, token_name)
            with cli.temp_token_file(create_fields, file_format) as path:
                cp = cli.create(self.waiter_url, token_name, create_flags=f'--{input_flag} {path}')
                self.assertEqual(0, cp.returncode, cp.stderr)
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.1, token_data['cpus'])
                self.assertEqual(128, token_data['mem'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_create_token_json(self):
        self.__test_create_token('json')

    def test_create_token_yaml(self):
        self.__test_create_token('yaml')

    def test_create_token_json_input(self):
        self.__test_create_token('json', 'input')

    def test_create_token_yaml_input(self):
        self.__test_create_token('yaml', 'input')

    def __test_update_token(self, file_format):
        token_name = self.token_name()
        create_fields = {'cpus': 0.1, 'mem': 128, 'cmd': 'foo'}
        update_fields = {'cpus': 0.2, 'mem': 256}
        util.post_token(self.waiter_url, token_name, create_fields)
        try:
            stdin = cli.dump(file_format, update_fields)

            cp = cli.update(self.waiter_url, token_name, update_flags=f'--{file_format} -', stdin=stdin)
            self.assertEqual(0, cp.returncode, cp.stderr)
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual(0.2, token_data['cpus'])
            self.assertEqual(256, token_data['mem'])
            self.assertEqual('foo', token_data['cmd'])

            # Test with data from a file
            util.post_token(self.waiter_url, token_name, create_fields)
            with cli.temp_token_file(update_fields, file_format) as path:
                cp = cli.update(self.waiter_url, token_name, update_flags=f'--{file_format} {path}')
                self.assertEqual(0, cp.returncode, cp.stderr)
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.2, token_data['cpus'])
                self.assertEqual(256, token_data['mem'])
                self.assertEqual('foo', token_data['cmd'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_update_token_json(self):
        self.__test_update_token('json')

    def test_update_token_yaml(self):
        self.__test_update_token('yaml')

    def __test_post_token_and_flags(self, file_format):
        token_name = self.token_name()
        update_fields = {'cpus': 0.2, 'mem': 256}
        with cli.temp_token_file(update_fields, file_format) as path:
            cp = cli.update(self.waiter_url, token_name,
                            update_flags=f'--{file_format} {path} --cpus 0.1')
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('cannot specify the same parameter in both an input file and token field flags at the '
                          'same time (cpus)', cli.stderr(cp))

            cp = cli.update(self.waiter_url, token_name,
                            update_flags=f'--{file_format} {path} --mem 128')
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('cannot specify the same parameter in both an input file and token field flags at the '
                          'same time (mem)', cli.stderr(cp))

            cp = cli.update(self.waiter_url, token_name,
                            update_flags=f'--{file_format} {path} --cpus 0.1 --mem 128')
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('cannot specify the same parameter in both an input file and token field flags',
                          cli.stderr(cp))
            self.assertIn('cpus', cli.stderr(cp))
            self.assertIn('mem', cli.stderr(cp))

            try:
                cp = cli.update(self.waiter_url, token_name,
                                update_flags=f'--{file_format} {path} --name foo --image bar')
                self.assertEqual(0, cp.returncode, cp.stderr)
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.2, token_data['cpus'])
                self.assertEqual(256, token_data['mem'])
                self.assertEqual('foo', token_data['name'])
                self.assertEqual('bar', token_data['image'])
            finally:
                util.delete_token(self.waiter_url, token_name)

    def test_post_token_json_and_flags(self):
        self.__test_post_token_and_flags('json')

    def test_post_token_yaml_and_flags(self):
        self.__test_post_token_and_flags('yaml')

    def __test_post_token_invalid(self, file_format):
        token_name = self.token_name()

        stdin = json.dumps([]).encode('utf8')
        cp = cli.update(self.waiter_url, token_name, update_flags=f'--{file_format} -', stdin=stdin)
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn(f'Input {file_format.upper()} must be a dictionary', cli.stderr(cp))

        stdin = '{"mem": 128'.encode('utf8')
        cp = cli.update(self.waiter_url, token_name, update_flags=f'--{file_format} -', stdin=stdin)
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn(f'Malformed {file_format.upper()}', cli.stderr(cp))

        with tempfile.NamedTemporaryFile(delete=True) as file:
            cp = cli.update(self.waiter_url, token_name, update_flags=f'--{file_format} {file.name}')
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn(f'Unable to load {file_format.upper()} from', cli.stderr(cp))

    def test_post_token_json_invalid(self):
        self.__test_post_token_invalid('json')

    def test_post_token_yaml_invalid(self):
        self.__test_post_token_invalid('yaml')

    def test_kill_service_id(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            service_id = util.ping_token(self.waiter_url, token_name)
            self.assertEqual(1, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli.kill(self.waiter_url, service_id, kill_flags='--service-id')
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Killing service', cli.stdout(cp))
            util.wait_until_no_services_for_token(self.waiter_url, token_name)
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_kill_bogus_service_id(self):
        cp = cli.kill(self.waiter_url, uuid.uuid4(), kill_flags='--service-id')
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('No matching data found', cli.stdout(cp))

    def test_kill_inactive_service_id(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            service_id = util.ping_token(self.waiter_url, token_name)
            util.kill_services_using_token(self.waiter_url, token_name)
            self.assertEqual(0, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli.kill(self.waiter_url, service_id, kill_flags='--service-id')
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('cannot be killed because it is already Inactive', cli.stdout(cp))
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def __test_init_basic(self, file_format):
        token_name = self.token_name()
        filename = str(uuid.uuid4())
        flags = f"--cmd '{util.default_cmd()}' --cmd-type shell --health-check-url /status " \
                f"--name {token_name} --{file_format} --file {filename} "
        cp = cli.init(self.waiter_url, init_flags=flags)
        self.assertEqual(0, cp.returncode, cp.stderr)
        self.assertIn(f'Writing token {file_format.upper()}', cli.stdout(cp))
        try:
            token_definition = util.load_file(file_format, filename)
            self.logger.info(f'Token definition: {cli.dump(file_format, token_definition)}')
            util.post_token(self.waiter_url, token_name, token_definition)
            try:
                token = util.load_token(self.waiter_url, token_name)
                self.assertEqual(token_name, token['name'])
                self.assertEqual('your-metric-group', token['metric-group'])
                self.assertEqual('shell', token['cmd-type'])
                self.assertEqual(util.default_cmd(), token['cmd'])
                self.assertEqual('your version', token['version'])
                self.assertEqual(0.1, token['cpus'])
                self.assertEqual(2048, token['mem'])
                self.assertEqual('/status', token['health-check-url'])
                self.assertEqual(120, token['concurrency-level'])
                self.assertEqual('*', token['permitted-user'])
                self.assertEqual(getpass.getuser(), token['run-as-user'])
                util.ping_token(self.waiter_url, token_name)
                self.assertEqual(1, len(util.services_for_token(self.waiter_url, token_name)))
            finally:
                util.delete_token(self.waiter_url, token_name, kill_services=True)
        finally:
            os.remove(filename)

    def test_init_basic_json(self):
        self.__test_init_basic('json')

    def test_init_basic_yaml(self):
        self.__test_init_basic('yaml')

    def test_implicit_init_args(self):
        cp = cli.init(init_flags='--help')
        self.assertEqual(0, cp.returncode, cp.stderr)
        self.assertIn('--cpus', cli.stdout(cp))
        self.assertNotIn('--https-redirect', cli.stdout(cp))
        self.assertNotIn('--fallback-period-secs', cli.stdout(cp))
        self.assertNotIn('--idle-timeout-mins', cli.stdout(cp))
        self.assertNotIn('--max-instances', cli.stdout(cp))
        self.assertNotIn('--restart-backoff-factor', cli.stdout(cp))
        token_name = self.token_name()
        with tempfile.NamedTemporaryFile(delete=True) as file:
            init_flags = (
                '--cmd-type shell '
                '--https-redirect true '
                '--cpus 0.1 '
                '--fallback-period-secs 10 '
                '--idle-timeout-mins 1 '
                '--max-instances 100 '
                '--restart-backoff-factor 1.1 '
                f'--file {file.name} '
                '--force')
            cp = cli.init(self.waiter_url, init_flags=init_flags)
            self.assertEqual(0, cp.returncode, cp.stderr)
            token_definition = util.load_file('json', file.name)
            self.logger.info(f'Token definition: {json.dumps(token_definition, indent=2)}')
            util.post_token(self.waiter_url, token_name, token_definition)
            try:
                token = util.load_token(self.waiter_url, token_name)
                self.assertEqual('your command', token['cmd'])
                self.assertEqual('shell', token['cmd-type'])
                self.assertEqual('your version', token['version'])
                self.assertEqual(0.1, token['cpus'])
                self.assertEqual(2048, token['mem'])
                self.assertTrue(token['https-redirect'])
                self.assertEqual(10, token['fallback-period-secs'])
                self.assertEqual(1, token['idle-timeout-mins'])
                self.assertEqual(100, token['max-instances'])
                self.assertEqual(1.1, token['restart-backoff-factor'])
            finally:
                util.delete_token(self.waiter_url, token_name)

    def test_init_existing_file(self):
        with tempfile.NamedTemporaryFile(delete=True) as file:
            self.assertTrue(os.path.isfile(file.name))
            cp = cli.init(self.waiter_url, init_flags=f"--cmd '{util.default_cmd()}' --file {file.name}")
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('There is already a file', cli.stderr(cp))
            cp = cli.init(self.waiter_url, init_flags=f"--cmd '{util.default_cmd()}' --file {file.name} --force")
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Writing token JSON', cli.stdout(cp))

    @pytest.mark.xfail
    def test_show_services_using_token(self):
        token_name = self.token_name()
        custom_fields = {
            'permitted-user': getpass.getuser(),
            'run-as-user': getpass.getuser(),
            'cpus': 0.1,
            'mem': 128
        }
        service_description_1 = util.minimal_service_description(**custom_fields)
        util.post_token(self.waiter_url, token_name, service_description_1)
        try:
            # Create 2 services, 1 running and 1 failing due to a bad command
            service_id_1 = util.ping_token(self.waiter_url, token_name)
            custom_fields['cmd'] = 'exit 1'
            custom_fields['cpus'] = 0.2
            custom_fields['mem'] = 256
            service_description_2 = util.minimal_service_description(**custom_fields)
            util.post_token(self.waiter_url, token_name, service_description_2)
            service_id_2 = util.ping_token(self.waiter_url, token_name, expected_status_code=503)

            # Run show with --json
            cp, services = cli.show_token_services('json', self.waiter_url, token_name=token_name)
            self.logger.info(f'Services: {json.dumps(services, indent=2)}')
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertEqual(2, len(services), services)
            service_1 = next(s for s in services if s['service-id'] == service_id_1)
            service_2 = next(s for s in services if s['service-id'] == service_id_2)
            self.assertEqual(service_description_1, service_1['service-description'])
            self.assertEqual(service_description_2, service_2['service-description'])
            self.assertEqual('Running', service_1['status'])
            self.assertIn(service_2['status'], ['Failing', 'Starting'])

            # Run show without --json
            cp = cli.show(self.waiter_url, token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIsNotNone(re.search('^# Services\\s+2$', cli.stdout(cp), re.MULTILINE))
            self.assertIsNotNone(re.search('^# Failing\\s+([01])$', cli.stdout(cp), re.MULTILINE))
            self.assertIsNotNone(re.search('^# Instances\\s+([12])$', cli.stdout(cp), re.MULTILINE))
            self.assertIsNotNone(re.search('^Total Memory\\s+(128|384) MiB$', cli.stdout(cp), re.MULTILINE))
            self.assertIsNotNone(re.search('^Total CPUs\\s+0\\.([13])$', cli.stdout(cp), re.MULTILINE))
            self.assertIsNotNone(re.search(f'^{service_id_1}.+Running.+Not Current$', cli.stdout(cp), re.MULTILINE))
            self.assertIsNotNone(re.search(f'^{service_id_2}.+(Failing|Starting).+Current$',
                                           cli.stdout(cp), re.MULTILINE))

            # Run show without --json and with --no-services
            cp = cli.show(self.waiter_url, token_name, show_flags='--no-services')
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertNotIn('# Services', cli.stdout(cp))
            self.assertNotIn('# Failing', cli.stdout(cp))
            self.assertNotIn('# Instances', cli.stdout(cp))
            self.assertNotIn('Total Memory', cli.stdout(cp))
            self.assertNotIn('Total CPUs', cli.stdout(cp))
            self.assertNotIn(service_id_1, cli.stdout(cp))
            self.assertNotIn(service_id_2, cli.stdout(cp))
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_tokens_basic(self):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description())
        try:
            # Ensure that tokens lists our token
            cp, tokens = cli.tokens_data(self.waiter_url)
            token_data = next(t for t in tokens if t['token'] == token_name)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertFalse(token_data['deleted'])
            self.assertFalse(token_data['maintenance'])

            # Delete the token
            util.delete_token(self.waiter_url, token_name)

            # Ensure that tokens does not list our token
            cp, tokens = cli.tokens_data(self.waiter_url)
            # The CLI returns 0 if there are any tokens 
            # owned by the user and 1 if there are none
            self.assertIn(cp.returncode, [0, 1], cp.stderr)
            self.assertFalse(any(t['token'] == token_name for t in tokens))
        finally:
            util.delete_token(self.waiter_url, token_name, assert_response=False)

    def __test_tokens_maintenance(self, expected_maintenance_value, service_config={}):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, util.minimal_service_description(**service_config))
        try:
            cp = cli.tokens(self.waiter_url)
            stdout = cli.stdout(cp)
            lines = stdout.split('\n')
            title_line = lines[0]
            maintenance_index = title_line.index('Maintenance')
            line_with_token = next(line for line in lines if token_name in line)
            token_maintenance = line_with_token[maintenance_index:maintenance_index + len(expected_maintenance_value)]
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertEqual(token_maintenance, expected_maintenance_value)
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_tokens_token_in_maintenance(self):
        service_config = {"maintenance": {"message": "custom message"}}
        self.__test_tokens_maintenance("True", service_config=service_config)

    def test_tokens_token_not_in_maintenance(self):
        self.__test_tokens_maintenance("False")

    def test_tokens_sorted(self):
        token_name_prefix = self.token_name()
        token_name_1 = f'{token_name_prefix}_foo'
        util.post_token(self.waiter_url, token_name_1, util.minimal_service_description())
        try:
            token_name_2 = f'{token_name_prefix}_bar'
            util.post_token(self.waiter_url, token_name_2, util.minimal_service_description())
            try:
                cp = cli.tokens(self.waiter_url)
                stdout = cli.stdout(cp)
                self.assertEqual(0, cp.returncode, cp.stderr)
                self.assertIn(token_name_1, stdout)
                self.assertIn(token_name_2, stdout)
                self.assertLess(stdout.index(token_name_2), stdout.index(token_name_1))
            finally:
                util.delete_token(self.waiter_url, token_name_2)
        finally:
            util.delete_token(self.waiter_url, token_name_1)

    def __test_create_token_containing_token_name(self, file_format):
        token_name = self.token_name()
        with cli.temp_token_file({'token': token_name, 'cpus': 0.1, 'mem': 128}, file_format) as path:
            cp = cli.create(self.waiter_url, create_flags=f'--{file_format} {path}')
            self.assertEqual(0, cp.returncode, cp.stderr)
            try:
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.1, token_data['cpus'])
                self.assertEqual(128, token_data['mem'])
            finally:
                util.delete_token(self.waiter_url, token_name)

    def test_create_token_json_containing_token_name(self):
        self.__test_create_token_containing_token_name('json')

    def test_create_token_yaml_containing_token_name(self):
        self.__test_create_token_containing_token_name('yaml')

    def test_create_nested_args_no_override(self):
        token_name = self.token_name()
        try:
            create_flags = f'{token_name} --metadata.foo bar --env.KEY_2 new_value_2 --env.KEY_3 new_value_3'
            cp = cli.create(self.waiter_url, flags='--verbose', create_flags=create_flags)
            self.assertEqual(0, cp.returncode, cp.stderr)
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual({'KEY_2': 'new_value_2',
                              'KEY_3': 'new_value_3'},
                             token_data['env'])
            self.assertEqual({'foo': 'bar'},
                             token_data['metadata'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_create_env_metadata_are_parsed_as_strings(self):
        token_name = self.token_name()
        try:
            create_flags = f'{token_name} --metadata.instances 5 --env.KEY_2 true --env.KEY_3 false'
            cp = cli.create(self.waiter_url, flags='--verbose', create_flags=create_flags)
            self.assertEqual(0, cp.returncode, cp.stderr)
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual({'KEY_2': 'true',
                              'KEY_3': 'false'},
                             token_data['env'])
            self.assertEqual({'instances': '5'},
                             token_data['metadata'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def __test_create_nested_args_parameter_override_success(self, file_format, create_existing_token=False):
        token_name = self.token_name()
        create_doc = {'token': token_name,
                      'cpus': 0.2,
                      'env': {'KEY_1': 'value_1',
                              'KEY_2': 'value_2'}}
        if create_existing_token:
            util.post_token(self.waiter_url, token_name, {'env': {'key': 'should_be_overridden'}})
        try:
            with cli.temp_token_file(create_doc, file_format) as path:
                explicit_create_flags = '--metadata.foo bar --env.KEY_2 new_value_2 --env.KEY_3 new_value_3'
                create_flags = f'--override {explicit_create_flags} --{file_format} {path}'
                cp = cli.create(self.waiter_url, flags='--verbose', create_flags=create_flags)
                self.assertEqual(0, cp.returncode, cp.stderr)
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.2, token_data['cpus'])
                self.assertEqual({'KEY_1': 'value_1',
                                  'KEY_2': 'new_value_2',
                                  'KEY_3': 'new_value_3'},
                                 token_data['env'])
                self.assertEqual({'foo': 'bar'},
                                 token_data['metadata'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_create_nested_args_json_parameter_override_success(self):
        self.__test_create_nested_args_parameter_override_success('json')

    def test_create_nested_args_yaml_parameter_override_success(self):
        self.__test_create_nested_args_parameter_override_success('yaml')

    def test_create_nested_args_parameter_override_success_with_existing_token(self):
        self.__test_create_nested_args_parameter_override_success('json', create_existing_token=True)

    def __test_update_token_containing_token_name(self, file_format):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1, 'mem': 128, 'cmd': 'foo'})
        try:
            with cli.temp_token_file({'token': token_name, 'cpus': 0.2, 'mem': 256}, file_format) as path:
                cp = cli.update(self.waiter_url, update_flags=f'--{file_format} {path}')
                self.assertEqual(0, cp.returncode, cp.stderr)
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.2, token_data['cpus'])
                self.assertEqual(256, token_data['mem'])
                self.assertEqual('foo', token_data['cmd'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_update_token_json_containing_token_name(self):
        self.__test_update_token_containing_token_name('json')

    def test_update_token_yaml_containing_token_name(self):
        self.__test_update_token_containing_token_name('yaml')

    def __test_update_token_override_fail(self, file_format):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1, 'mem': 128, 'cmd': 'foo'})
        try:
            with cli.temp_token_file({'token': token_name, 'cpus': 0.2, 'mem': 256}, file_format) as path:
                cp = cli.update(self.waiter_url, update_flags=f'--cpus 0.3 --{file_format} {path}')
                self.assertEqual(1, cp.returncode, cp.stderr)
                stderr = cli.stderr(cp)
                err_msg = 'You cannot specify the same parameter in both an input file ' \
                          'and token field flags at the same time'
                self.assertIn(err_msg, stderr)
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.1, token_data['cpus'])
                self.assertEqual(128, token_data['mem'])
                self.assertEqual('foo', token_data['cmd'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_update_token_json_override_fail(self):
        self.__test_update_token_override_fail('json')

    def test_update_token_yaml_override_fail(self):
        self.__test_update_token_override_fail('yaml')

    def __test_update_token_override_success(self, file_format, diff_token_in_file):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1, 'mem': 128, 'cmd': 'foo'})
        try:
            token_in_file = f'abc_{token_name}' if diff_token_in_file else token_name
            with cli.temp_token_file({'token': token_in_file, 'cpus': 0.2, 'mem': 256}, file_format) as path:
                update_flags = f'--override --cpus 0.3 --{file_format} {path}'
                if diff_token_in_file:
                    update_flags = f'{update_flags} {token_name}'
                cp = cli.update(self.waiter_url, flags='--verbose', update_flags=update_flags)
                self.assertEqual(0, cp.returncode, cp.stderr)
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.3, token_data['cpus'])
                self.assertEqual(256, token_data['mem'])
                self.assertEqual('foo', token_data['cmd'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_update_token_json_parameter_override_success(self):
        self.__test_update_token_override_success('json', False)

    def test_update_token_yaml_parameter_override_success(self):
        self.__test_update_token_override_success('yaml', False)

    def test_update_token_json_token_override_success(self):
        self.__test_update_token_override_success('json', True)

    def test_update_token_yaml_token_override_success(self):
        self.__test_update_token_override_success('yaml', True)

    def __test_update_token_override_failure(self, file_format, diff_token_in_file, update_flags="--cpus 0.3"):
        token_name = self.token_name()
        token_fields = {'cpus': 0.1, 'mem': 128, 'cmd': 'foo', 'env': {'FOO': 'BAR'}}
        util.post_token(self.waiter_url, token_name, token_fields)
        try:
            token_in_file = f'abc_{token_name}' if diff_token_in_file else token_name
            with cli.temp_token_file({'token': token_in_file, **token_fields}, file_format) as path:
                update_flags = f'--no-override {update_flags} --{file_format} {path}'
                if diff_token_in_file:
                    update_flags = f'{update_flags} {token_name}'
                cp = cli.update(self.waiter_url, flags='--verbose', update_flags=update_flags)
                self.assertEqual(1, cp.returncode, cp.stderr)
                stderr = cli.stderr(cp)
                err_msg = 'You cannot specify the same parameter'
                self.assertIn(err_msg, stderr)
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_update_token_json_parameter_override_failure(self):
        self.__test_update_token_override_failure('json', False)

    def test_update_token_yaml_parameter_override_failure(self):
        self.__test_update_token_override_failure('yaml', False)

    def test_update_token_json_token_override_failure(self):
        self.__test_update_token_override_failure('json', True)

    def test_update_token_yaml_token_override_failure(self):
        self.__test_update_token_override_failure('yaml', True)

    def test_post_token_over_specified_token_name(self):
        token_name = self.token_name()
        with cli.temp_token_file({'token': token_name}) as path:
            cp = cli.create(self.waiter_url, token_name, create_flags=f'--json {path}')
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('cannot specify the token name both as an argument and in the input file',
                          cli.stderr(cp))

    def test_post_token_no_token_name(self):
        cp = cli.create(self.waiter_url)
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('must specify the token name', cli.stderr(cp))
        with cli.temp_token_file({'cpus': 0.1}) as path:
            cp = cli.create(self.waiter_url, create_flags=f'--json {path}')
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('must specify the token name', cli.stderr(cp))

    def test_implicit_args_lenient_parsing(self):
        token_name = self.token_name()
        cp = cli.create(self.waiter_url, token_name, create_flags='--cpus 0.1 --foo-level HIGH --bar-rate LOW')
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('Unsupported key(s)', cli.stderr(cp))
        self.assertIn('foo-level', cli.stderr(cp))
        self.assertIn('bar-rate', cli.stderr(cp))

    def test_update_nested_args_no_override(self):
        token_name = self.token_name()
        initial_token_config = {'cmd': 'foo',
                                'cpus': 0.1,
                                'env': {'KEY_1': 'value_1',
                                        'KEY_2': 'value_2'},
                                'mem': 128}
        util.post_token(self.waiter_url, token_name, initial_token_config)
        try:
            update_flags = f'{token_name} --metadata.foo bar --env.KEY_2 new_value_2 --env.KEY_3 new_value_3'
            cp = cli.update(self.waiter_url, flags='--verbose', update_flags=update_flags)
            self.assertEqual(0, cp.returncode, cp.stderr)
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual(0.1, token_data['cpus'])
            self.assertEqual(128, token_data['mem'])
            self.assertEqual('foo', token_data['cmd'])
            self.assertEqual({'KEY_1': 'value_1',
                              'KEY_2': 'new_value_2',
                              'KEY_3': 'new_value_3'},
                             token_data['env'])
            self.assertEqual({'foo': 'bar'},
                             token_data['metadata'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def __test_update_nested_args_with_overrides_success(self, file_format):
        token_name = self.token_name()
        initial_token_config = {'cmd': 'foo',
                                'cpus': 0.1,
                                'env': {'KEY_1': 'value_1',
                                        'KEY_2': 'value_2'},
                                'mem': 128}
        token_update_doc = {'token': token_name,
                            'cpus': 0.2,
                            'mem': 256,
                            'metadata': {'key1': 'value1'}}
        explicit_update_flags = '--metadata.foo bar --env.KEY_2 new_value_2 --env.KEY_3 new_value_3'
        util.post_token(self.waiter_url, token_name, initial_token_config)
        try:
            with cli.temp_token_file(token_update_doc, file_format) as path:
                update_flags = f'--override {explicit_update_flags} --{file_format} {path}'
                cp = cli.update(self.waiter_url, flags='--verbose', update_flags=update_flags)
                self.assertEqual(0, cp.returncode, cp.stderr)
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.2, token_data['cpus'])
                self.assertEqual(256, token_data['mem'])
                # override with file will shallow merge
                self.assertEqual({'KEY_2': 'new_value_2',
                                  'KEY_3': 'new_value_3'},
                                 token_data['env'])
                self.assertEqual({'foo': 'bar',
                                  'key1': 'value1'},
                                 token_data['metadata'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_update_nested_args_json_with_overrides_success(self):
        self.__test_update_nested_args_with_overrides_success('json')

    def test_update_nested_args_yaml_with_overrides_success(self):
        self.__test_update_nested_args_with_overrides_success('yaml')

    def test_update_nested_args_json_with_overrides_failure(self):
        self.__test_update_token_override_failure('json', False, update_flags="--env.FOO testing")

    def test_update_nested_args_yaml_with_overrides_failure(self):
        self.__test_update_token_override_failure('yaml', False, update_flags="--env.FOO testing")

    def test_show_service_current(self):
        token_name_1 = self.token_name()
        token_name_2 = self.token_name()
        iso_8601_time = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()
        custom_fields = {
            'owner': getpass.getuser(),
            'cluster': 'test_show_service_current',
            'root': 'test_show_service_current',
            'last-update-user': getpass.getuser(),
            'last-update-time': iso_8601_time
        }
        token_definition = util.minimal_service_description(**custom_fields)

        # Post two identical tokens with different names
        util.post_token(self.waiter_url, token_name_1, token_definition, update_mode_admin=True, assert_response=False)
        util.post_token(self.waiter_url, token_name_2, token_definition, update_mode_admin=True, assert_response=False)
        try:
            # Assert that their etags match
            etag_1 = util.load_token_with_headers(self.waiter_url, token_name_1)[1]['ETag']
            etag_2 = util.load_token_with_headers(self.waiter_url, token_name_2)[1]['ETag']
            self.assertEqual(etag_1, etag_2)

            # Create service A from the two tokens
            service_id_a = util.ping_token(self.waiter_url, f'{token_name_1},{token_name_2}')

            # Update token #2 only and assert that their etags don't match
            token_definition['cpus'] += 0.1
            util.post_token(self.waiter_url, token_name_2, token_definition, update_mode_admin=True,
                            assert_response=False, etag=etag_1)
            etag_1 = util.load_token_with_headers(self.waiter_url, token_name_1)[1]['ETag']
            etag_2 = util.load_token_with_headers(self.waiter_url, token_name_2)[1]['ETag']
            self.assertNotEqual(etag_1, etag_2)

            # Create service B from the two tokens
            service_id_b = util.ping_token(self.waiter_url, f'{token_name_1},{token_name_2}')

            # Update token #1 to match token #2 and assert that their etags match
            util.post_token(self.waiter_url, token_name_1, token_definition, update_mode_admin=True,
                            assert_response=False, etag=etag_1)
            etag_1 = util.load_token_with_headers(self.waiter_url, token_name_1)[1]['ETag']
            etag_2 = util.load_token_with_headers(self.waiter_url, token_name_2)[1]['ETag']
            self.assertEqual(etag_1, etag_2)

            # Update token #2 only and assert that their etags don't match
            token_definition['cpus'] += 0.1
            util.post_token(self.waiter_url, token_name_2, token_definition, update_mode_admin=True,
                            assert_response=False, etag=etag_1)
            etag_1 = util.load_token_with_headers(self.waiter_url, token_name_1)[1]['ETag']
            etag_2 = util.load_token_with_headers(self.waiter_url, token_name_2)[1]['ETag']
            self.assertNotEqual(etag_1, etag_2)

            # Create service C from the two tokens
            service_id_c = util.ping_token(self.waiter_url, f'{token_name_1},{token_name_2}')

            # For both tokens, only service C should be "current"
            cp = cli.show(self.waiter_url, token_name_1)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIsNotNone(re.search(f'^{service_id_a}.+Not Current$', cli.stdout(cp), re.MULTILINE))
            self.assertIsNotNone(re.search(f'^{service_id_b}.+Not Current$', cli.stdout(cp), re.MULTILINE))
            self.assertIsNotNone(re.search(f'^{service_id_c}.+Current$', cli.stdout(cp), re.MULTILINE))
            cp = cli.show(self.waiter_url, token_name_2)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIsNotNone(re.search(f'^{service_id_a}.+Not Current$', cli.stdout(cp), re.MULTILINE))
            self.assertIsNotNone(re.search(f'^{service_id_b}.+Not Current$', cli.stdout(cp), re.MULTILINE))
            self.assertIsNotNone(re.search(f'^{service_id_c}.+Current$', cli.stdout(cp), re.MULTILINE))
        finally:
            util.delete_token(self.waiter_url, token_name_1, kill_services=True)
            util.delete_token(self.waiter_url, token_name_2, kill_services=True)

    def test_create_token_output_stdout(self):
        token_name = self.token_name()
        token_fields = {
            'cpus': 0.2,
            'mem': 256,
            'run-as-user': 'FAKE_USERNAME'
        }
        file_format = 'yaml'
        stdin = cli.dump(file_format, token_fields)
        flags = f'--output - --{file_format} -'
        try:
            cp = cli.create(self.waiter_url, token_name, flags='-v', stdin=stdin, create_flags=flags)
            self.assertEqual(0, cp.returncode, cp.stderr)
            util.load_token(self.waiter_url, token_name, expected_status_code=404)
            stdout = cli.stdout(cp)
            self.assertIn('Token configuration (as json) is:', stdout)
            json_str = stdout[(stdout.rindex('is:') + 3):]
            printed_token_fields = json.loads(json_str)
            self.assertEqual(token_fields, printed_token_fields)
        finally:
            util.delete_token(self.waiter_url, token_name, expected_status_code=404)

    def test_create_token_output_json(self):
        token_name = self.token_name()
        token_fields = {
            'cpus': 0.2,
            'mem': 256,
            'run-as-user': 'FAKE_USERNAME'
        }
        file_format = 'yaml'
        stdin = cli.dump(file_format, token_fields)
        try:
            with tempfile.NamedTemporaryFile(delete=True, suffix='.json') as output_file:
                flags = f'--output {output_file.name} --{file_format} -'
                cp = cli.create(self.waiter_url, token_name, flags='-v', stdin=stdin, create_flags=flags)
                self.assertEqual(0, cp.returncode, cp.stderr)
                util.load_token(self.waiter_url, token_name, expected_status_code=404)
                stdout = cli.stdout(cp)
                self.assertIn(f'Writing token configuration (as json) to {output_file.name}', stdout)
                printed_token_fields = util.load_file('json', output_file.name)
                self.assertEqual(token_fields, printed_token_fields)
        finally:
            util.delete_token(self.waiter_url, token_name, expected_status_code=404)

    def test_update_token_output_stdout(self):
        token_name = self.token_name()
        base_fields = {
            'cpus': 1.0,
            'health-check-url': '/health',
            'permitted-user': '*'
        }
        util.post_token(self.waiter_url, token_name, base_fields)

        token_fields = {
            'cpus': 0.2,
            'mem': 256,
            'run-as-user': 'FAKE_USERNAME'
        }
        file_format = 'yaml'
        stdin = cli.dump(file_format, token_fields)
        flags = f'--output - --{file_format} -'
        try:
            cp = cli.update(self.waiter_url, token_name, flags='-v', stdin=stdin, update_flags=flags)
            self.assertEqual(0, cp.returncode, cp.stderr)
            util.load_token(self.waiter_url, token_name)
            stdout = cli.stdout(cp)
            self.assertIn('Token configuration (as json) is:', stdout)
            json_str = stdout[(stdout.rindex('is:') + 3):]
            printed_token_fields = json.loads(json_str)
            expected_fields = {**base_fields, **token_fields, 'owner': getpass.getuser()}
            self.assertEqual(expected_fields, printed_token_fields)
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_update_token_output_yaml(self):
        token_name = self.token_name()
        base_fields = {
            'cpus': 1.0,
            'health-check-url': '/health',
            'permitted-user': '*'
        }
        util.post_token(self.waiter_url, token_name, base_fields)

        token_fields = {
            'cpus': 0.2,
            'mem': 256,
            'run-as-user': 'FAKE_USERNAME'
        }
        file_format = 'yaml'
        stdin = cli.dump(file_format, token_fields)
        try:
            with tempfile.NamedTemporaryFile(delete=True, suffix='.yaml') as output_file:
                flags = f'--output {output_file.name} --{file_format} -'
                cp = cli.update(self.waiter_url, token_name, flags='-v', stdin=stdin, update_flags=flags)
                self.assertEqual(0, cp.returncode, cp.stderr)
                util.load_token(self.waiter_url, token_name)
                stdout = cli.stdout(cp)
                self.assertIn(f'Writing token configuration (as yaml) to {output_file.name}', stdout)
                printed_token_fields = util.load_file('yaml', output_file.name)
                expected_fields = {**base_fields, **token_fields, 'owner': getpass.getuser()}
                self.assertEqual(expected_fields, printed_token_fields)
        finally:
            util.delete_token(self.waiter_url, token_name)

    def __test_create_update_token_admin_mode(self, action, token_name, admin_mode):
        token_fields = {
            'cpus': 0.2,
            'mem': 256,
            'run-as-user': 'FAKE_USERNAME'
        }
        file_format = 'yaml'
        stdin = cli.dump(file_format, token_fields)
        flags = f'{"--admin " if admin_mode else ""}--{file_format} -'
        temp_env = os.environ.copy()
        temp_env["WAITER_ADMIN"] = 'true'
        cp = getattr(cli, action)(self.waiter_url, token_name, flags='-v', stdin=stdin, env=temp_env,
                                  **{f'{action}_flags': flags})
        if admin_mode:
            try:
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0, cp.returncode, cp.stderr)
                self.assertIn('update-mode=admin', cli.stderr(cp))
                self.assertIn(f'Attempting to {action} token in ADMIN mode', cli.stdout(cp))
                for key, value in token_fields.items():
                    self.assertEqual(value, token_data[key])
            finally:
                util.delete_token(self.waiter_url, token_name)
        else:
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('Cannot run as user.', cli.decode(cp.stderr))

    def test_create_token_admin_mode(self):
        self.__test_create_update_token_admin_mode('create', self.token_name(), True)

    def test_create_token_no_admin_mode(self):
        self.__test_create_update_token_admin_mode('create', self.token_name(), False)

    def test_update_token_admin_mode(self):
        token_name = self.token_name()
        create_fields = {'cpus': 0.1, 'mem': 128, 'cmd': 'foo'}
        util.post_token(self.waiter_url, token_name, create_fields)
        self.__test_create_update_token_admin_mode('update', token_name, True)

    def test_update_token_no_admin_mode(self):
        self.__test_create_update_token_admin_mode('update', self.token_name(), False)

    def __test_create_update_token_context_missing_data_failure(self, action):
        token_name = self.token_name()
        context_fields = {'fee': 'bar', 'fie': 'baz', 'foe': 'fum'}
        try:
            with cli.temp_token_file({**context_fields}, 'yaml') as path:
                flags = f'--context {path}'
                cp = getattr(cli, action)(self.waiter_url, token_name, flags='-v', **{f'{action}_flags': flags})
                self.assertEqual(1, cp.returncode, cp.stderr)
                stderr = cli.stderr(cp)
                err_msg = '--context file can only be used when a data file is specified via --input, --json, or --yaml'
                self.assertIn(err_msg, stderr)
        finally:
            # the token should not have been created, but cleaning up in case the test failed
            util.delete_token(self.waiter_url, token_name, assert_response=False)

    def test_create_token_context_missing_data_failure(self):
        self.__test_create_update_token_context_missing_data_failure('create')

    def test_update_token_context_missing_data_failure(self):
        self.__test_create_update_token_context_missing_data_failure('update')

    def __test_create_update_token_context_missing_file_failure(self, action, file_format):
        token_name = self.token_name()
        token_fields = {'cmd': 'foo-bar', 'cpus': 0.2, 'mem': 256}
        try:
            with cli.temp_token_file({**token_fields}, file_format) as token_path:
                with tempfile.NamedTemporaryFile(delete=True) as file:
                    flags = f'--context {file.name} --{file_format} {token_path}'
                    cp = getattr(cli, action)(self.waiter_url, token_name, flags='-v', **{f'{action}_flags': flags})
                    self.assertEqual(1, cp.returncode, cp.stderr)
                    stderr = cli.stderr(cp)
                    err_msg = f'Unable to load context from {file.name}'
                    self.assertIn(err_msg, stderr)
        finally:
            # the token should not have been created, but cleaning up in case the test failed
            util.delete_token(self.waiter_url, token_name, assert_response=False)

    def test_create_token_context_missing_file_failure_json_data(self):
        self.__test_create_update_token_context_missing_file_failure('create', 'json')

    def test_create_token_context_missing_file_failure_yaml_data(self):
        self.__test_create_update_token_context_missing_file_failure('create', 'yaml')

    def test_update_token_context_missing_file_failure_json_data(self):
        self.__test_create_update_token_context_missing_file_failure('update', 'json')

    def test_update_token_context_missing_file_failure_yaml_data(self):
        self.__test_create_update_token_context_missing_file_failure('update', 'yaml')

    def __test_create_update_token_context_bad_format_failure(self, action, file_format):
        token_name = self.token_name()
        token_fields = {'cmd': 'foo-bar', 'cpus': 0.2, 'mem': 256}
        try:
            with cli.temp_token_file({**token_fields}, file_format) as token_path:
                with cli.temp_file('foo-bar') as context_path:
                    flags = f'--context {context_path} --{file_format} {token_path}'
                    cp = getattr(cli, action)(self.waiter_url, token_name, flags='-v', **{f'{action}_flags': flags})
                    self.assertEqual(1, cp.returncode, cp.stderr)
                    stderr = cli.stderr(cp)
                    err_msg = 'Provided context file must evaluate to a dictionary, instead it is foo-bar'
                    self.assertIn(err_msg, stderr)
        finally:
            # the token should not have been created, but cleaning up in case the test failed
            util.delete_token(self.waiter_url, token_name, assert_response=False)

    def test_create_token_context_bad_format_failure_json_data(self):
        self.__test_create_update_token_context_bad_format_failure('create', 'json')

    def test_create_token_context_bad_format_failure_yaml_data(self):
        self.__test_create_update_token_context_bad_format_failure('create', 'yaml')

    def test_update_token_context_bad_format_failure_json_data(self):
        self.__test_create_update_token_context_bad_format_failure('update', 'json')

    def test_update_token_context_bad_format_failure_yaml_data(self):
        self.__test_create_update_token_context_bad_format_failure('update', 'yaml')

    def __test_create_update_token_context_success(self, action, file_format):
        token_name = self.token_name()
        context_fields = {'fee': 'bar', 'fie': 'baz', 'foe': 'fum'}
        token_fields = {'cmd': '${fee}-${fie}', 'cpus': 0.2, 'mem': 256, 'metadata': {'foe': '${foe}'}}
        try:
            if action == 'update':
                util.post_token(self.waiter_url, token_name, {'cpus': 0.1, 'mem': 128})
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.1, token_data['cpus'])
                self.assertEqual(128, token_data['mem'])

            with cli.temp_token_file({**token_fields}, file_format) as token_path:
                with cli.temp_token_file({**context_fields}, 'yaml') as context_path:
                    flags = f'--context {context_path} --{file_format} {token_path}'
                    cp = getattr(cli, action)(self.waiter_url, token_name, flags='-v', **{f'{action}_flags': flags})
                    self.assertEqual(0, cp.returncode, cp.stderr)
                    stdout = cli.stdout(cp)
                    out_msg = f'Successfully {action}d {token_name}'
                    self.assertIn(out_msg, stdout)
                    token_data = util.load_token(self.waiter_url, token_name)
                    self.assertEqual('bar-baz', token_data['cmd'])
                    self.assertEqual(0.2, token_data['cpus'])
                    self.assertEqual(256, token_data['mem'])
                    self.assertEqual({'foe': 'fum'}, token_data['metadata'])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_create_token_context_success_json_data(self):
        self.__test_create_update_token_context_success('create', 'json')

    def test_create_token_context_success_yaml_data(self):
        self.__test_create_update_token_context_success('create', 'yaml')

    def test_update_token_context_success_json_data(self):
        self.__test_create_update_token_context_success('update', 'json')

    def test_update_token_context_success_yaml_data(self):
        self.__test_create_update_token_context_success('update', 'yaml')

    def __test_create_update_token_context_missing_variable_failure(self, action, file_format):
        token_name = self.token_name()
        context_fields = {'fee': 'bar', 'fie': 'baz'}
        token_fields = {'cmd': '${fee}-${fie}-${foe}', 'cpus': 0.2, 'mem': 256, 'metadata': {'foe': '${foe}'}}
        try:
            if action == 'update':
                util.post_token(self.waiter_url, token_name, {'cpus': 0.1, 'mem': 128})
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.1, token_data['cpus'])
                self.assertEqual(128, token_data['mem'])

            with cli.temp_token_file({**token_fields}, file_format) as token_path:
                with cli.temp_token_file({**context_fields}, 'yaml') as context_path:
                    flags = f'--context {context_path} --{file_format} {token_path}'
                    cp = getattr(cli, action)(self.waiter_url, token_name, flags='-v', **{f'{action}_flags': flags})
                    self.assertEqual(1, cp.returncode, cp.stderr)
                    stderr = cli.stderr(cp)
                    err_msg = "Error when processing template: missing variable 'foe'"
                    self.assertIn(err_msg, stderr)
        finally:
            util.delete_token(self.waiter_url, token_name, assert_response=False)

    def test_create_token_context_missing_variable_failure_json_data(self):
        self.__test_create_update_token_context_missing_variable_failure('create', 'json')

    def test_create_token_context_missing_variable_failure_yaml_data(self):
        self.__test_create_update_token_context_missing_variable_failure('create', 'yaml')

    def test_update_token_context_missing_variable_failure_json_data(self):
        self.__test_create_update_token_context_missing_variable_failure('update', 'json')

    def test_update_token_context_missing_variable_failure_yaml_data(self):
        self.__test_create_update_token_context_missing_variable_failure('update', 'yaml')

    def __test_create_update_token_context_override_variable_success(self, action, file_format):
        token_name = self.token_name()
        context_fields = {'fee': 'bar', 'fie': 'baz'}
        token_fields = {'cmd': '${fee}-${fie}-${foe}', 'cpus': 0.2, 'mem': 256, 'metadata': {'foe': '${foe}'}}
        try:
            if action == 'update':
                util.post_token(self.waiter_url, token_name, {'cpus': 0.1, 'mem': 128})
                token_data = util.load_token(self.waiter_url, token_name)
                self.assertEqual(0.1, token_data['cpus'])
                self.assertEqual(128, token_data['mem'])

            with cli.temp_token_file({**token_fields}, file_format) as token_path:
                with cli.temp_token_file({**context_fields}, 'yaml') as context_path:
                    flags = f'--context {context_path} --context.fie box --context.foe fum --{file_format} {token_path}'
                    cp = getattr(cli, action)(self.waiter_url, token_name, flags='-v', **{f'{action}_flags': flags})
                    self.assertEqual(0, cp.returncode, cp.stderr)
                    stdout = cli.stdout(cp)
                    out_msg = f'Successfully {action}d {token_name}'
                    self.assertIn(out_msg, stdout)
                    token_data = util.load_token(self.waiter_url, token_name)
                    self.assertEqual('bar-box-fum', token_data['cmd'])
                    self.assertEqual(0.2, token_data['cpus'])
                    self.assertEqual(256, token_data['mem'])
                    self.assertEqual({'foe': 'fum'}, token_data['metadata'])
        finally:
            util.delete_token(self.waiter_url, token_name, assert_response=False)

    def test_create_token_context_override_variable_success_json_data(self):
        self.__test_create_update_token_context_override_variable_success('create', 'json')

    def test_create_token_context_override_variable_success_yaml_data(self):
        self.__test_create_update_token_context_override_variable_success('create', 'yaml')

    def test_update_token_context_override_variable_success_json_data(self):
        self.__test_create_update_token_context_override_variable_success('update', 'json')

    def test_update_token_context_override_variable_success_yaml_data(self):
        self.__test_create_update_token_context_override_variable_success('update', 'yaml')

    def run_maintenance_start_test(self, cli_fn, start_args='', ping_token=False):
        token_name = self.token_name()
        token_fields = util.minimal_service_description()
        custom_maintenance_message = "custom maintenance message"
        util.post_token(self.waiter_url, token_name, token_fields)
        try:
            if ping_token:
                cp = cli.ping(self.waiter_url, token_name)
                self.assertEqual(0, cp.returncode, cp.stderr)
                self.assertIn('Pinging token', cli.stdout(cp))
                self.assertIn('successful', cli.stdout(cp))
                util.wait_until_services_for_token(self.waiter_url, token_name, 1)
                self.assertEqual(1, len(util.services_for_token(self.waiter_url, token_name)))
            cp = cli_fn(token_name, self.waiter_url, maintenance_flags=f'{start_args} "{custom_maintenance_message}"')
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn('Maintenance mode activated', cli.stdout(cp))
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual({'message': custom_maintenance_message}, token_data['maintenance'])
            for key, value in token_fields.items():
                self.assertEqual(value, token_data[key])
            if ping_token:
                num_services = 1 if '--no-kill' in start_args else 0
                self.assertEqual(num_services,
                                 len(util.wait_until_services_for_token(self.waiter_url, token_name, num_services)))
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_maintenance_start_basic(self):
        self.run_maintenance_start_test(partial(cli.maintenance, 'start'))

    def test_maintenance_start_no_service_ask_kill(self):
        self.run_maintenance_start_test(partial(cli.maintenance, 'start'), start_args='--ask-kill')

    def test_maintenance_start_no_service_force_kill(self):
        self.run_maintenance_start_test(partial(cli.maintenance, 'start'), start_args='--force-kill')

    def test_maintenance_start_no_service_no_kill(self):
        self.run_maintenance_start_test(partial(cli.maintenance, 'start'), start_args='--no-kill')

    def test_maintenance_start_ping_service_ask_kill(self):
        self.run_maintenance_start_test(partial(cli.maintenance, 'start'), start_args='--ask-kill', ping_token=True)

    def test_maintenance_start_ping_service_force_kill(self):
        self.run_maintenance_start_test(partial(cli.maintenance, 'start'), start_args='--force-kill', ping_token=True)

    def test_maintenance_start_ping_service_no_kill(self):
        self.run_maintenance_start_test(partial(cli.maintenance, 'start'), start_args='--no-kill', ping_token=True)

    def test_maintenance_start_nonexistent_token(self):
        token_name = self.token_name()
        custom_maintenance_message = "custom maintenance message"
        cp = cli.maintenance('start', token_name, self.waiter_url,
                             maintenance_flags=f'"{custom_maintenance_message}"')
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('The token does not exist. You must create it first.', cli.stderr(cp))

    def test_maintenance_start_no_cluster(self):
        custom_maintenance_message = "custom maintenance message"
        self.__test_no_cluster(partial(cli.maintenance, 'start',
                                       maintenance_flags=f'"{custom_maintenance_message}"'))

    def run_maintenance_stop_no_ping_test(self, cli_fn):
        token_name = self.token_name()
        token_fields = {'cpus': 0.1, 'mem': 128, 'cmd': 'foo'}
        custom_maintenance_message = "custom maintenance message"
        util.post_token(self.waiter_url, token_name,
                        {**token_fields, 'maintenance': {'message': custom_maintenance_message}})
        try:
            cp = cli_fn( token_name, self.waiter_url, maintenance_flags='--no-ping')
            self.assertEqual(0, cp.returncode, cp.stderr)
            stdout = cli.stdout(cp)
            self.assertNotIn(f'Pinging token {token_name}', stdout)
            self.assertNotIn(f'Ping successful', stdout)
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual(None, token_data.get('maintenance', None))
            for key, value in token_fields.items():
                self.assertEqual(value, token_data[key])
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_maintenance_stop_no_ping(self):
        self.run_maintenance_stop_no_ping_test(partial(cli.maintenance, 'stop'))

    def run_maintenance_stop_with_ping_test(self, cli_fn):
        token_name = self.token_name()
        token_fields = util.minimal_service_description()
        custom_maintenance_message = "custom maintenance message"
        util.post_token(self.waiter_url, token_name,
                        {**token_fields, 'maintenance': {'message': custom_maintenance_message}})
        try:
            cp = cli_fn(token_name, self.waiter_url)
            stdout = cli.stdout(cp)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn(f'Pinging token {token_name}', stdout)
            self.assertIn('Ping successful', stdout)
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual(None, token_data.get('maintenance', None))
            for key, value in token_fields.items():
                self.assertEqual(value, token_data[key])
            self.assertEqual(1, len(util.wait_until_services_for_token(self.waiter_url, token_name, 1)))
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_maintenance_stop_with_ping(self):
        self.run_maintenance_stop_with_ping_test(partial(cli.maintenance, 'stop'))

    def run_maintenance_stop_with_ping_no_wait_test(self, cli_fn):
        token_name = self.token_name()
        token_fields = util.minimal_service_description()
        token_fields['cmd'] = f"sleep 30 && {token_fields['cmd']}"
        custom_maintenance_message = "custom maintenance message"
        util.post_token(self.waiter_url, token_name,
                        {**token_fields, 'maintenance': {'message': custom_maintenance_message}})
        try:
            cp = cli_fn(token_name, self.waiter_url, maintenance_flags='--no-wait')
            stdout = cli.stdout(cp)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn(f'Pinging token {token_name}', stdout)
            self.assertIn('Service is currently Starting', stdout)
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual(None, token_data.get('maintenance', None))
            for key, value in token_fields.items():
                self.assertEqual(value, token_data[key])
            self.assertEqual(1, len(util.wait_until_services_for_token(self.waiter_url, token_name, 1)))
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_maintenance_stop_with_ping_no_wait(self):
        self.run_maintenance_stop_with_ping_no_wait_test(partial(cli.maintenance, 'stop'))

    def test_maintenance_stop_no_cluster(self):
        self.__test_no_cluster(partial(cli.maintenance, 'stop'))

    def run_maintenance_stop_enforce_check_not_in_maintenance_test(self, cli_fn):
        token_name = self.token_name()
        util.post_token(self.waiter_url, token_name, {'cpus': 0.1, 'mem': 128, 'cmd': 'foo'})
        try:
            cp = cli_fn(token_name, self.waiter_url, maintenance_flags='--check')
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn('Token is not in maintenance mode', cli.stderr(cp))
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_maintenance_stop_enforce_check_not_in_maintenance(self):
        self.run_maintenance_stop_enforce_check_not_in_maintenance_test(partial(cli.maintenance, 'stop'))

    def run_maintenance_stop_skip_check_not_in_maintenance_test(self, cli_fn):
        token_name = self.token_name()
        token_fields = util.minimal_service_description()
        util.post_token(self.waiter_url, token_name, token_fields)
        try:
            cp = cli_fn(token_name, self.waiter_url)
            stdout = cli.stdout(cp)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn(f'Token {token_name} does not have maintenance mode activated', stdout)
            self.assertIn(f'Pinging token {token_name}', stdout)
            self.assertIn('Service is currently Running', stdout)
            token_data = util.load_token(self.waiter_url, token_name)
            self.assertEqual(None, token_data.get('maintenance', None))
            for key, value in token_fields.items():
                self.assertEqual(value, token_data[key])
            self.assertEqual(1, len(util.wait_until_services_for_token(self.waiter_url, token_name, 1)))
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_maintenance_stop_skip_check_not_in_maintenance(self):
        self.run_maintenance_stop_skip_check_not_in_maintenance_test(partial(cli.maintenance, 'stop'))

    def __test_maintenance_check(self, maintenance_active):
        token_name = self.token_name()
        output = f'{token_name} is {"" if maintenance_active else "not "}in maintenance mode'
        cli_return_code = 0 if maintenance_active else 1
        if maintenance_active:
            util.post_token(self.waiter_url, token_name,
                            {'cpus': 0.1, 'mem': 128, 'cmd': 'foo', 'maintenance': {'message': 'custom message'}})
        else:
            util.post_token(self.waiter_url, token_name, {'cpus': 0.1, 'mem': 128, 'cmd': 'foo'})
        try:
            cp = cli.maintenance('check', token_name, self.waiter_url)
            self.assertEqual(cli_return_code, cp.returncode, cp.stderr)
            self.assertIn(output, cli.stdout(cp))
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_maintenance_check_not_in_maintenance_mode(self):
        self.__test_maintenance_check(False)

    def test_maintenance_check_in_maintenance_mode(self):
        self.__test_maintenance_check(True)

    def test_maintenance_no_sub_command(self):
        cp = cli.maintenance('', '')
        cp_help = cli.maintenance('', '', maintenance_flags='-h')
        self.assertEqual(0, cp.returncode, cp.stderr)
        self.assertEqual(cli.stdout(cp_help), cli.stdout(cp))

    def __test_ssh(self, get_possible_instances_fn, command_to_run=None, stdin=None, min_instances=1, admin=False,
                   ssh_flags=None, container_name=None, is_failed_instance=False, test_service=False,
                   test_instance=False, multiple_services=False, quick=False, expect_no_data=False,
                   expect_no_instances=False, expect_out_of_range=False):
        token_name = self.token_name()
        token_fields = util.minimal_service_description()
        token_fields['min-instances'] = min_instances
        if is_failed_instance:
            token_fields['cmd'] = 'this_is_an_invalid_command'
        try:
            if multiple_services:
                token_new_fields = util.minimal_service_description()
                util.post_token(self.waiter_url, token_name, token_new_fields)
                util.ping_token(self.waiter_url, token_name)
            util.post_token(self.waiter_url, token_name, token_fields)
            service_id = util.ping_token(self.waiter_url, token_name,
                                         expected_status_code=503 if is_failed_instance else 200)
            if is_failed_instance:
                goal_fn = lambda insts: 0 < len(insts['failed-instances']) and \
                                        0 == len(insts['killed-instances'])
            else:
                goal_fn = lambda insts: min_instances == len(insts['active-instances']) and \
                                        0 == len(insts['failed-instances']) and \
                                        0 == len(insts['killed-instances'])
            util.wait_until_routers_service(self.waiter_url, service_id, lambda service: goal_fn(service['instances']))
            instances = util.instances_for_service(self.waiter_url, service_id)
            env = os.environ.copy()
            env['WAITER_SSH'] = 'echo'
            env['WAITER_KUBECTL'] = 'echo'
            if admin:
                env['WAITER_ADMIN'] = 'true'
            possible_instances = get_possible_instances_fn(service_id, instances)
            ssh_flags = [ssh_flags] if ssh_flags else []
            if quick:
                ssh_flags.append('-q')
            if container_name:
                ssh_flags.append(f'--container-name {container_name}')
            if test_instance:
                possible_instances = possible_instances[0:1]
                ssh_dest = possible_instances[0]['id']
                ssh_flags.append('-i')
            elif test_service:
                ssh_dest = service_id
                ssh_flags.append('-s')
            else:
                ssh_dest = token_name
            cp = cli.ssh(self.waiter_url, ssh_dest, stdin=stdin, ssh_command=command_to_run,
                         ssh_flags=' '.join(ssh_flags),
                         env=env)
            stdout = cli.stdout(cp)
            if expect_out_of_range:
                self.assertEqual(1, cp.returncode, cp.stderr)
                self.assertIn('Input is out of range!', cli.stderr(cp))
            elif expect_no_data:
                self.assertEqual(1, cp.returncode, cp.stderr)
                self.assertIn('No matching data found', stdout)
            elif expect_no_instances:
                self.assertEqual(1, cp.returncode, cp.stderr)
                self.assertIn(f'There are no relevant instances using service id {service_id}', stdout)
            else:
                self.assertEqual(0, cp.returncode, cp.stderr)
                ssh_instance = util.get_ssh_instance_from_output(self.waiter_url, possible_instances, stdout,
                                                                 container_name=container_name,
                                                                 command_to_run=command_to_run)
                self.assertIsNotNone(ssh_instance,
                                     msg=f"None of the possible instances {possible_instances} were detected in ssh "
                                         f"command output: \n{stdout}")
        finally:
            util.delete_token(self.waiter_url, token_name, kill_services=True)

    def test_ssh_instance_id(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], test_instance=True)

    def test_ssh_instance_id_failed_instance(self):
        self.__test_ssh(lambda _, instances: instances['failed-instances'], is_failed_instance=True,
                        test_instance=True)

    def test_ssh_instance_id_custom_cmd(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], test_instance=True,
                        command_to_run='ls -al')

    def test_ssh_instance_id_custom_cmd_failed_instance(self):
        self.__test_ssh(lambda _, instances: instances['failed-instances'], is_failed_instance=True,
                        test_instance=True, command_to_run='ls -al')

    def test_ssh_instance_id_no_instance(self):
        self.__test_ssh(lambda service_id, _: [{'id': service_id + '.nonexistent'}], test_instance=True,
                        expect_no_data=True)

    def test_ssh_instance_id_no_service(self):
        instance_id_no_service = "a.a"
        cp = cli.ssh(self.waiter_url, instance_id_no_service, ssh_flags='-i')
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('No matching data found', cli.stdout(cp))

    def test_ssh_service_id_single_instance(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], test_service=True)

    def test_ssh_service_id_no_relevant_instances(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], test_service=True,
                        ssh_flags='--no-active', expect_no_instances=True)

    def test_ssh_service_id_multiple_instances(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], min_instances=2,
                        stdin='1\n'.encode('utf8'), test_service=True)

    def test_ssh_service_id_invalid_prompt_input(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], min_instances=2,
                        stdin='-123\n'.encode('utf8'), test_service=True, expect_out_of_range=True)

    def test_ssh_service_id_non_existent_service(self):
        service_id = "nonexistent"
        cp = cli.ssh(self.waiter_url, service_id, ssh_flags='-s')
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('No matching data found', cli.stdout(cp))

    def test_ssh_service_id_quick(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'],  min_instances=2, test_service=True,
                        quick=True)

    def test_ssh_token_single_instance(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'])

    def test_ssh_token_multiple_services_sorted(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], stdin='1\n'.encode('utf8'),
                        multiple_services=True)

    def test_ssh_token_multiple_instances(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], min_instances=2,
                        stdin='1\n'.encode('utf8'))

    def test_ssh_token_multiple_services_instances(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], min_instances=2, multiple_services=True,
                        stdin='1\n1\n'.encode('utf8'))

    def test_ssh_token_multiple_services_instances_quick(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], min_instances=2, multiple_services=True,
                        quick=True)

    def test_ssh_token_custom_container(self):
        self.__test_ssh(lambda _, instances: instances['active-instances'], admin=True,
                        container_name='waiter-files')

    def test_ssh_token_invalid_token(self):
        token_name = "nonexistent"
        cp = cli.ssh(self.waiter_url, token_name)
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('No matching data found', cli.stdout(cp))

    def test_ssh_token_invalid_token_quick(self):
        token_name = "nonexistent"
        cp = cli.ssh(self.waiter_url, token_name, ssh_flags='-q')
        self.assertEqual(1, cp.returncode, cp.stderr)
        self.assertIn('The token does not exist. You must create it first.', cli.stderr(cp))

    def __test_ssh_token_no_services(self, ssh_flags=None):
        token_name = self.token_name()
        token_fields = util.minimal_service_description()
        util.post_token(self.waiter_url, token_name, token_fields)
        try:
            cp = cli.ssh(self.waiter_url, token_name, ssh_flags=ssh_flags)
            self.assertEqual(1, cp.returncode, cp.stderr)
            self.assertIn(f'There are no services using token {token_name}', cli.stdout(cp))
        finally:
            util.delete_token(self.waiter_url, token_name)

    def test_ssh_token_no_services(self):
        self.__test_ssh_token_no_services()

    def test_ssh_token_no_services_quick(self):
        self.__test_ssh_token_no_services(ssh_flags='-q')

    def test_start_no_cluster(self):
        self.__test_no_cluster(partial(cli.start))

    def test_start_no_ping(self):
        self.run_maintenance_stop_no_ping_test(cli.start)

    def test_start_with_ping(self):
        self.run_maintenance_stop_with_ping_test(cli.start)

    def test_start_with_ping_no_wait(self):
        self.run_maintenance_stop_with_ping_no_wait_test(cli.start)

    def test_start_enforce_check_not_in_maintenance(self):
        self.run_maintenance_stop_enforce_check_not_in_maintenance_test(cli.start)

    def test_start_skip_check_not_in_maintenance(self):
        self.run_maintenance_stop_skip_check_not_in_maintenance_test(cli.start)

    def test_stop_no_cluster(self):
        custom_maintenance_message = "custom maintenance message"
        self.__test_no_cluster(partial(cli.stop, maintenance_flags=f'"{custom_maintenance_message}"'))

    def test_stop_basic(self):
        self.run_maintenance_start_test(cli.stop)

    def test_stop_no_service_ask_kill(self):
        self.run_maintenance_start_test(cli.stop, start_args='--ask-kill')

    def test_stop_no_service_force_kill(self):
        self.run_maintenance_start_test(cli.stop, start_args='--force-kill')

    def test_stop_no_service_no_kill(self):
        self.run_maintenance_start_test(cli.stop, start_args='--no-kill')

    def test_stop_ping_service_ask_kill(self):
        self.run_maintenance_start_test(cli.stop, start_args='--ask-kill', ping_token=True)

    def test_stop_ping_service_force_kill(self):
        self.run_maintenance_start_test(cli.stop, start_args='--force-kill', ping_token=True)

    def test_stop_ping_service_no_kill(self):
        self.run_maintenance_start_test(cli.stop, start_args='--no-kill', ping_token=True)
