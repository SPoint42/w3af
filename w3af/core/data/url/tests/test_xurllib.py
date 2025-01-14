# -*- coding: utf-8 -*-
"""
test_xurllib.py

Copyright 2011 Andres Riancho

This file is part of w3af, http://w3af.org/ .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
"""
import os
import ssl
import time
import Queue
import types
import unittest
import httpretty
import SocketServer

from multiprocessing.dummy import Process
from nose.plugins.attrib import attr
from mock import Mock, patch

from w3af import ROOT_PATH

from w3af.core.data.url.extended_urllib import ExtendedUrllib
from w3af.core.data.url.constants import MAX_ERROR_COUNT, SOCKET_ERROR_DELAY
from w3af.core.data.url.tests.helpers.upper_daemon import UpperDaemon
from w3af.core.data.url.tests.helpers.ssl_daemon import RawSSLDaemon, SSLServer
from w3af.core.data.parsers.url import URL
from w3af.core.data.dc.urlencoded_form import URLEncodedForm
from w3af.core.data.dc.headers import Headers
from w3af.core.data.url.HTTPResponse import DEFAULT_WAIT_TIME

from w3af.core.controllers.misc.get_unused_port import get_unused_port
from w3af.core.controllers.ci.moth import get_moth_http
from w3af.core.controllers.misc.temp_dir import get_temp_dir
from w3af.core.controllers.exceptions import (ScanMustStopByUserRequest,
                                              HTTPRequestException,
                                              ScanMustStopException)


@attr('moth')
@attr('smoke')
class TestXUrllib(unittest.TestCase):

    MOTH_MESSAGE = '<title>moth: vulnerable web application</title>'

    def setUp(self):
        self.uri_opener = ExtendedUrllib()
    
    def tearDown(self):
        self.uri_opener.end()
        
    def test_basic(self):
        url = URL(get_moth_http())
        http_response = self.uri_opener.GET(url, cache=False)
        
        self.assertIn(self.MOTH_MESSAGE, http_response.body)
        
        self.assertGreaterEqual(http_response.id, 1)
        self.assertNotEqual(http_response.id, None)

    def test_cache(self):
        url = URL(get_moth_http())
        http_response = self.uri_opener.GET(url)
        self.assertIn(self.MOTH_MESSAGE, http_response.body)

        url = URL(get_moth_http())
        http_response = self.uri_opener.GET(url)
        self.assertIn(self.MOTH_MESSAGE, http_response.body)

    def test_qs_params(self):
        url = URL(get_moth_http('/audit/xss/simple_xss.py?text=123456abc'))
        http_response = self.uri_opener.GET(url, cache=False)
        self.assertIn('123456abc', http_response.body)

        url = URL(get_moth_http('/audit/xss/simple_xss.py?text=root:x:0'))
        http_response = self.uri_opener.GET(url, cache=False)
        self.assertIn('root:x:0', http_response.body)

    def test_POST(self):
        url = URL(get_moth_http('/audit/xss/simple_xss_form.py'))

        data = URLEncodedForm()
        data['text'] = ['123456abc']

        http_response = self.uri_opener.POST(url, data, cache=False)
        self.assertIn('123456abc', http_response.body)

    def test_POST_special_chars(self):
        url = URL(get_moth_http('/audit/xss/simple_xss_form.py'))
        test_data = u'abc<def>"-á-'

        data = URLEncodedForm()
        data['text'] = [test_data]

        http_response = self.uri_opener.POST(url, data, cache=False)
        self.assertIn(test_data, http_response.body)

    def test_unknown_domain(self):
        url = URL('http://longsitethatdoesnotexistfoo.com/')
        self.assertRaises(HTTPRequestException, self.uri_opener.GET, url)

    def test_url_port_closed(self):
        # TODO: Change 2312 by an always closed/non-http port
        url = URL('http://127.0.0.1:2312/')
        self.assertRaises(HTTPRequestException, self.uri_opener.GET, url)

    def test_url_port_not_http(self):
        upper_daemon = UpperDaemon(EmptyTCPHandler)
        upper_daemon.start()
        upper_daemon.wait_for_start()

        port = upper_daemon.get_port()

        url = URL('http://127.0.0.1:%s/' % port)

        try:
            self.uri_opener.GET(url)
        except HTTPRequestException, hre:
            self.assertEqual(hre.value, "Bad HTTP response status line: ''")
        else:
            self.assertTrue(False, 'Expected HTTPRequestException.')

    def test_url_port_not_http_many(self):
        upper_daemon = UpperDaemon(EmptyTCPHandler)
        upper_daemon.start()
        upper_daemon.wait_for_start()

        port = upper_daemon.get_port()

        url = URL('http://127.0.0.1:%s/' % port)
        http_request_e = 0
        scan_must_stop_e = 0

        for _ in xrange(MAX_ERROR_COUNT):
            try:
                self.uri_opener.GET(url)
            except HTTPRequestException:
                http_request_e += 1
            except ScanMustStopException, smse:
                scan_must_stop_e += 1
                break
            except Exception, e:
                msg = 'Not expecting "%s".'
                self.assertTrue(False, msg % e.__class__.__name__)

        self.assertEqual(scan_must_stop_e, 1)
        self.assertEqual(http_request_e, 9)

    def test_get_wait_time(self):
        """
        Asserts that all the responses coming out of the extended urllib have a
        get_wait_time different from the default.
        """
        url = URL(get_moth_http())
        http_response = self.uri_opener.GET(url, cache=False)
        self.assertNotEqual(http_response.get_wait_time(), DEFAULT_WAIT_TIME)

    def test_ssl_tls_1_0(self):
        ssl_daemon = RawSSLDaemon(Ok200Handler, ssl_version=ssl.PROTOCOL_TLSv1)
        ssl_daemon.start()
        ssl_daemon.wait_for_start()

        port = ssl_daemon.get_port()

        url = URL('https://127.0.0.1:%s/' % port)

        resp = self.uri_opener.GET(url)
        self.assertEqual(resp.get_body(), Ok200Handler.body)

    def test_ssl_v23(self):
        ssl_daemon = RawSSLDaemon(Ok200Handler, ssl_version=ssl.PROTOCOL_SSLv23)
        ssl_daemon.start()
        ssl_daemon.wait_for_start()

        port = ssl_daemon.get_port()

        url = URL('https://127.0.0.1:%s/' % port)

        resp = self.uri_opener.GET(url)
        self.assertEqual(resp.get_body(), Ok200Handler.body)

    def test_ssl_v3(self):
        ssl_daemon = RawSSLDaemon(Ok200Handler, ssl_version=ssl.PROTOCOL_SSLv3)
        ssl_daemon.start()
        ssl_daemon.wait_for_start()

        port = ssl_daemon.get_port()

        url = URL('https://127.0.0.1:%s/' % port)

        resp = self.uri_opener.GET(url)
        self.assertEqual(resp.get_body(), Ok200Handler.body)

    def test_stop(self):
        self.uri_opener.stop()
        url = URL(get_moth_http())
        self.assertRaises(ScanMustStopByUserRequest, self.uri_opener.GET, url)

    def test_pause_stop(self):
        self.uri_opener.pause(True)
        self.uri_opener.stop()
        url = URL(get_moth_http())
        self.assertRaises(ScanMustStopByUserRequest, self.uri_opener.GET, url)

    def test_pause(self):
        output = Queue.Queue()
        self.uri_opener.pause(True)

        def send(uri_opener, output):
            url = URL(get_moth_http())
            try:
                http_response = uri_opener.GET(url)
                output.put(http_response)
            except:
                output.put(None)

        th = Process(target=send, args=(self.uri_opener, output))
        th.daemon = True
        th.start()

        self.assertRaises(Queue.Empty, output.get, True, 2)

    def test_pause_unpause(self):
        output = Queue.Queue()
        self.uri_opener.pause(True)

        def send(uri_opener, output):
            url = URL(get_moth_http())
            try:
                http_response = uri_opener.GET(url)
                output.put(http_response)
            except:
                output.put(None)

        th = Process(target=send, args=(self.uri_opener, output))
        th.daemon = True
        th.start()

        self.assertRaises(Queue.Empty, output.get, True, 2)

        self.uri_opener.pause(False)

        http_response = output.get()
        self.assertNotIsInstance(http_response, types.NoneType,
                                 'Error in send thread.')
        
        th.join()
        
        self.assertEqual(http_response.get_code(), 200)
        self.assertIn(self.MOTH_MESSAGE, http_response.body)
    
    def test_removes_cache(self):
        url = URL(get_moth_http())
        self.uri_opener.GET(url, cache=False)
        
        # Please note that this line, together with the tearDown() act as
        # a test for a "double call to end()".
        self.uri_opener.end()
        
        db_fmt = 'db_unittest-%s'
        trace_fmt = 'db_unittest-%s_traces/'
        temp_dir = get_temp_dir()
        
        for i in xrange(100):
            test_db_path = os.path.join(temp_dir, db_fmt % i)
            test_trace_path = os.path.join(temp_dir, trace_fmt % i)
            self.assertFalse(os.path.exists(test_db_path), test_db_path)
            self.assertFalse(os.path.exists(test_trace_path), test_trace_path)
    
    def test_special_char_header(self):
        url = URL(get_moth_http('/core/headers/echo-headers.py'))
        header_content = u'name=ábc'
        headers = Headers([('Cookie', header_content)])
        http_response = self.uri_opener.GET(url, cache=False, headers=headers)
        self.assertIn(header_content, http_response.body)

    @attr('internet')
    def test_bad_file_descriptor_8125(self):
        """
        8125 is basically an issue with the way HTTP SSL connections handle the
        Connection: Close header. If at any point the URL in this test starts
        to fail, I just need to find another which sends that header.

        Also, see the test_bad_file_descriptor_8125_mock test.

        :see: https://github.com/andresriancho/w3af/issues/8125
        """
        self.uri_opener.settings.set_max_http_retries(0)
        url = URL('https://www.factoriadigital.com/hosting/wordpress')
        http_response = self.uri_opener.GET(url, cache=False)
        self.assertIn('Soporte', http_response.body)

    def test_bad_file_descriptor_8125_local(self):
        """
        :see: https://github.com/andresriancho/w3af/issues/8125
        """
        port = get_unused_port()
        raw_http_response = "HTTP/1.1 200 Ok\r\n"\
                            "Connection: close\r\n"\
                            "Content-Type: text/html\r\n"\
                            "Content-Length: 3\r\n\r\nabc"
        certfile = os.path.join(ROOT_PATH, 'plugins', 'tests', 'audit',
                                'certs', 'invalid_cert.pem')

        s = SSLServer('localhost', port, certfile,
                      http_response=raw_http_response)
        s.start()

        body = 'abc'
        mock_url = 'https://localhost:%s/' % port
        url = URL(mock_url)
        http_response = self.uri_opener.GET(url, cache=False)

        self.assertEqual(body, http_response.body)
        s.stop()

    def test_rate_limit_high(self):
        self.rate_limit_generic(500, 0.01, 0.4)

    def test_rate_limit_low(self):
        self.rate_limit_generic(1, 1, 2.2)

    def test_rate_limit_zero(self):
        self.rate_limit_generic(0, 0.01, 0.4)

    @httpretty.activate
    def rate_limit_generic(self, max_requests_per_second, _min, _max):
        mock_url = 'http://mock/'
        url = URL(mock_url)
        httpretty.register_uri(httpretty.GET, mock_url, body='Body')

        start_time = time.time()

        with patch.object(self.uri_opener.settings, 'get_max_requests_per_second') as mrps_mock:
            mrps_mock.return_value = max_requests_per_second

            self.uri_opener.GET(url, cache=False)
            self.uri_opener.GET(url, cache=False)

        httpretty.reset()

        end_time = time.time()
        elapsed_time = end_time - start_time
        self.assertGreaterEqual(elapsed_time, _min)
        self.assertLessEqual(elapsed_time, _max)


class EmptyTCPHandler(SocketServer.BaseRequestHandler):
    def handle(self):
        self.data = self.request.recv(1024).strip()
        self.request.sendall('')


class TimeoutTCPHandler(SocketServer.BaseRequestHandler):
    def handle(self):
        self.data = self.request.recv(1024).strip()
        time.sleep(60)
        self.request.sendall('')


class Ok200Handler(SocketServer.BaseRequestHandler):
    body = 'abc'

    def handle(self):
        self.data = self.request.recv(1024).strip()
        self.request.sendall('HTTP/1.0 200 Ok\r\n'
                             'Connection: Close\r\n'
                             'Content-Length: 3\r\n'
                             '\r\n' + self.body)