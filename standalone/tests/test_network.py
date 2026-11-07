import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ge_wine_network import connections,endpoint
from ge_wine_logging import RunLog
from ge_wine_sessions import identity
from ge_wine_dedup import canonical


class NetworkTests(unittest.TestCase):
    @unittest.skipIf(sys.platform=='darwin','macOS may deny socket inspection without privileges')
    def test_real_owned_socket_and_unrelated_socket_exclusion(self):
        listener=socket.socket();self.addCleanup(listener.close)
        listener.bind(('127.0.0.1',0));listener.listen()
        pid=os.getpid();stamp=identity(pid)
        rows,errors=connections({(pid,stamp):dict(name='owned-test')})
        self.assertFalse(errors)
        matching=[row for row in rows.values() if row['local']['port']==listener.getsockname()[1]]
        self.assertTrue(matching)
        self.assertEqual(matching[0]['pid'],pid)
        self.assertEqual(connections({})[0],{})
        self.assertEqual(connections({(pid,'invalid-stamp'):dict(name='reused-pid')})[0],{})

    def test_default_logging_lifecycle_dedup_and_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            log=RunLog(dict(WINEPREFIX=temporary),['test'],temporary,'standard','c'*32)
            row=dict(pid=999,start_ticks='1',protocol='tcp',local=dict(ip='127.0.0.1',port=11),remote=dict(ip='127.0.0.1',port=12),state='ESTABLISHED')
            try:
                self.assertTrue(log.network_enabled)
                with patch('ge_wine_network.connections',return_value=({('socket',):row},[])):
                    log.snapshot_network({});log.snapshot_network({})
                with patch('ge_wine_network.connections',return_value=({},['denied'])):
                    log.snapshot_network({})
                log.publish_repetitions()
                events=[json.loads(line) for line in (log.path/'network.jsonl').read_text().splitlines()]
                self.assertEqual([event['event'] for event in events],['connection_observed','connection_no_longer_observed','network_coverage_error'])
                self.assertIn('no packets',log.metadata['network_logging']['coverage'])
            finally:log.close()

    def test_opt_out_does_not_inspect_sockets(self):
        with tempfile.TemporaryDirectory() as temporary:
            log=RunLog(dict(WINEPREFIX=temporary),['test'],temporary,'standard','d'*32,network_log=False)
            try:
                with patch('ge_wine_network.connections',side_effect=AssertionError('disabled')):log.snapshot()
                self.assertEqual((log.path/'network.jsonl').read_text(),'')
            finally:log.close()

    def test_users_module_example_collapses_and_payload_values_survive(self):
        body=b'trace:module:build_import_name found L"kernelbase.dll" for L"api-ms-win-core-handle-l1-1-0.dll"'
        lines=[b'152689.648:00dc:00e0:'+body,b'52689.676:0020:0024:'+body,b'52689.782:00f4:00f8:'+body,b'52690.473:010c:0110:'+body,b'52690.518:0130:0134:'+body]
        self.assertEqual(len({canonical(line)[0] for line in lines}),1)
        self.assertNotEqual(canonical(lines[0])[0],canonical(lines[0].replace(b'kernelbase',b'kernel32'))[0])
        self.assertNotEqual(canonical(b'1.00:0001:0002:err:call addr=0x1234')[0],canonical(b'2.00:0003:0004:err:call addr=0x1235')[0])
        self.assertEqual(canonical(b'0001:0002:'+body)[0],body)
        self.assertEqual(canonical(b'1234:5678:customer-data')[0],b'1234:5678:customer-data')
