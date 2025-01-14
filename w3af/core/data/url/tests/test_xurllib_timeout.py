# -*- coding: utf-8 -*-
"""
test_xurllib_timeout.py

Copyright 2015 Andres Riancho

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
import time
import unittest
import SocketServer

from nose.plugins.attrib import attr
from nose.plugins.skip import SkipTest
from mock import Mock

from w3af.core.data.url.extended_urllib import ExtendedUrllib
from w3af.core.data.url.constants import (MAX_ERROR_COUNT, DEFAULT_TIMEOUT,
                                          TIMEOUT_ADJUST_LIMIT,
                                          TIMEOUT_MULT_CONST)
from w3af.core.data.url.tests.helpers.upper_daemon import UpperDaemon
from w3af.core.data.url.tests.helpers.ssl_daemon import RawSSLDaemon
from w3af.core.data.url.tests.test_xurllib import TimeoutTCPHandler
from w3af.core.data.parsers.url import URL

from w3af.core.controllers.exceptions import (HTTPRequestException,
                                              ScanMustStopException)


@attr('moth')
@attr('smoke')
class TestXUrllibTimeout(unittest.TestCase):

    def setUp(self):
        self.uri_opener = ExtendedUrllib()

    def tearDown(self):
        self.uri_opener.end()

    def test_timeout(self):
        upper_daemon = UpperDaemon(TimeoutTCPHandler)
        upper_daemon.start()
        upper_daemon.wait_for_start()

        port = upper_daemon.get_port()

        url = URL('http://127.0.0.1:%s/' % port)

        self.uri_opener.settings.set_configured_timeout(0.5)
        self.uri_opener.clear_timeout()
        # We can mock this because it's being tested at TestXUrllibDelayOnError
        self.uri_opener._pause_on_http_error = Mock()
        start = time.time()

        try:
            self.uri_opener.GET(url)
        except HTTPRequestException, hre:
            self.assertEqual(hre.message, 'HTTP timeout error')
        except Exception, e:
            msg = 'Not expecting: "%s"'
            self.assertTrue(False, msg % e.__class__.__name__)
        else:
            self.assertTrue(False, 'Expected HTTPRequestException.')

        end = time.time()
        self.uri_opener.settings.set_default_values()
        self.assertLess(end-start, 1.5)

    def test_timeout_ssl(self):
        ssl_daemon = RawSSLDaemon(TimeoutTCPHandler)
        ssl_daemon.start()
        ssl_daemon.wait_for_start()

        port = ssl_daemon.get_port()

        url = URL('https://127.0.0.1:%s/' % port)

        self.uri_opener.settings.set_configured_timeout(1)
        self.uri_opener.clear_timeout()
        start = time.time()

        self.assertRaises(HTTPRequestException, self.uri_opener.GET, url)

        end = time.time()
        self.uri_opener.settings.set_default_values()

        #   We Skip this part because openssl doesn't allow us to use timeouts
        #   https://github.com/andresriancho/w3af/issues/7989
        #
        #   Don't Skip at the beginning of the test because we want to be able
        #   to test that timeout exceptions are at least handled by xurllib
        raise SkipTest('See https://github.com/andresriancho/w3af/issues/7989')
        #self.assertLess(end-start, 3)

    def test_timeout_many(self):
        upper_daemon = UpperDaemon(TimeoutTCPHandler)
        upper_daemon.start()
        upper_daemon.wait_for_start()

        port = upper_daemon.get_port()

        self.uri_opener.settings.set_configured_timeout(0.5)
        self.uri_opener.clear_timeout()
        # We can mock this because it's being tested at TestXUrllibDelayOnError
        self.uri_opener._pause_on_http_error = Mock()

        url = URL('http://127.0.0.1:%s/' % port)
        http_request_e = 0
        scan_stop_e = 0

        for _ in xrange(MAX_ERROR_COUNT):
            try:
                self.uri_opener.GET(url)
            except HTTPRequestException, hre:
                http_request_e += 1
                self.assertEqual(hre.message, 'HTTP timeout error')
            except ScanMustStopException:
                scan_stop_e += 1
                self.assertTrue(True)
                break
            except Exception, e:
                msg = 'Not expecting: "%s"'
                self.assertTrue(False, msg % e.__class__.__name__)
            else:
                self.assertTrue(False, 'Expecting timeout')
        else:
            self.assertTrue(False, 'Expected ScanMustStopException')

        self.uri_opener.settings.set_default_values()
        self.assertEqual(http_request_e, 4)
        self.assertEqual(scan_stop_e, 1)

    def test_timeout_auto_adjust(self):
        upper_daemon = UpperDaemon(Ok200SmallDelayHandler)
        upper_daemon.start()
        upper_daemon.wait_for_start()

        port = upper_daemon.get_port()

        # Enable timeout auto-adjust
        self.uri_opener.settings.set_configured_timeout(0)
        self.uri_opener.clear_timeout()

        # We can mock this because it's being tested at TestXUrllibDelayOnError
        self.uri_opener._pause_on_http_error = Mock()

        # Mock to verify the calls
        self.uri_opener.set_timeout = Mock()

        # Make sure we start from the desired timeout value
        self.assertEqual(self.uri_opener.get_timeout('127.0.0.1'),
                         DEFAULT_TIMEOUT)

        url = URL('http://127.0.0.1:%s/' % port)
        sent_requests = 0

        for _ in xrange(TIMEOUT_ADJUST_LIMIT * 2):
            try:
                self.uri_opener.GET(url)
            except Exception:
                raise
            else:
                sent_requests += 1
                if self.uri_opener.set_timeout.call_count:
                    break

        self.assertEqual(self.uri_opener.set_timeout.call_count, 1)

        rtt = self.uri_opener.get_average_rtt()[0]
        adjusted_tout = self.uri_opener.set_timeout.call_args[0][0]
        expected_tout = TIMEOUT_MULT_CONST * rtt
        delta = rtt * 0.2

        self.assertGreaterEqual(adjusted_tout, expected_tout - delta)
        self.assertLessEqual(adjusted_tout, expected_tout + delta)
        self.assertLess(adjusted_tout, DEFAULT_TIMEOUT)
        self.assertEqual(sent_requests, TIMEOUT_ADJUST_LIMIT + 1)


class Ok200SmallDelayHandler(SocketServer.BaseRequestHandler):
    body = 'abc'
    sleep = 0.1

    def handle(self):
        self.data = self.request.recv(1024).strip()
        time.sleep(self.sleep)
        self.request.sendall('HTTP/1.0 200 Ok\r\n'
                             'Connection: Close\r\n'
                             'Content-Length: 3\r\n'
                             '\r\n' + self.body)