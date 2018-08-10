# -*- coding: utf-8 -*-
import sys
import os
import socket
import ssl
import select
import brotli
import http.client
import urllib.parse
import threading
import gzip
import zlib
import time
import json
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from io import BytesIO
from subprocess import Popen, PIPE
from html.parser import HTMLParser
from colors import red,green,blue,cyan,yellow

is_modified = False


def join_with_script_dir(path):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), path)




class Request(object):
    def __init__(self, id, command, host, path, request_version, header, query, cookie, body):
        self.id = id
        self.command = command
        self.host = host
        self.path = path
        self.request_version = request_version
        self.header = header
        self.query = query
        self.cookie = cookie
        self.body = body

    def print_req(self):
        print("\nREQUEST number:" + str(self.id) + '\n' + str(self.command) + " " + str(self.path) + " " + (self.request_version) + "\n\nHEADER\n\n" + str(self.header) + "\n\nQUERY-PARAMETER \n\n" + self.query + "\n\nCOOKIE\n\n" + self.cookie + "\n\nBODY REQUEST\n\n"+ self.body)

    def __str__(self):
        return  str(self.command) + " " + str(self.path)+ " " + str(self.request_version) + '\r\n' + str(self.header) + '\r\n' + str(self.body) + '\r\n'
1
class Response(object):
    def __init__(self, id,  version, status, reason, header, set_cookie, body):
        self.id = id
        self.version = version
        self.status = status
        self.reason = reason
        self.header = header
        self.set_cookie = set_cookie
        self.body = body

    def print_res(self):
        print("\nRESPONSE number: " + str(self.id) + '\n' + str(self.version) + " " + str(self.status) + " " + str(self.reason) + "\nHEADER\n" + str(self.header) + "\n\nSET-COOKIE\n" + str(self.set_cookie) + "\nRESPONSE BODY\n" + str(self.body))

    def __str__(self):
        return str(self.version) + ' ' + str(self.status) + ' ' + str(self.reason) + "\r\n" + str(self.header) + '\r\n' + str(self.body)

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    address_family = socket.AF_INET
    daemon_threads = True

    def handle_error(self, request, client_address):
        # surpress socket/ssl related errors
        cls, e = sys.exc_info()[:2]
        if cls is socket.error or cls is ssl.SSLError:
            pass
        else:
            return HTTPServer.handle_error(self, request, client_address)


class ProxyRequestHandler(BaseHTTPRequestHandler):
    cakey = join_with_script_dir('ca.key')
    cacert = join_with_script_dir('ca.crt')
    certkey = join_with_script_dir('cert.key')
    certdir = join_with_script_dir('certs/')
    timeout = 20
    lock = threading.Lock()
    reqlist = []
    reslist = []
    request = Request
    mode = 'Sniffing'

    def log_message(self, format, *args):
        return

    def __init__(self, *args, **kwargs):
        self.tls = threading.local()
        self.tls.conns = {}
        BaseHTTPRequestHandler.__init__(self, *args, **kwargs)

    def get_req(self):
        return self.reqlist

    def get_res(self):
        return self.reslist

    def log_error(self, format, *args):
        # surpress "Request timed out: timeout('timed out',)"
        if isinstance(args[0], socket.timeout):
            return

        self.log_message(format, *args)

    def do_CONNECT(self):
        if os.path.isfile(self.cakey) and os.path.isfile(self.cacert) and os.path.isfile(self.certkey) and os.path.isdir(self.certdir):
            self.connect_intercept()
        else:
            self.connect_relay()

    def connect_intercept(self):
        hostname = self.path.split(':')[0]
        certpath = "%s/%s.crt" % (self.certdir.rstrip('/'), hostname)

        with self.lock:
            if not os.path.isfile(certpath):
                epoch = "%d" % (time.time() * 1000)
                p1 = Popen(["openssl", "req", "-new", "-key", self.certkey, "-subj", "/CN=%s" % hostname], stdout=PIPE)
                p2 = Popen(["openssl", "x509", "-req", "-days", "3650", "-CA", self.cacert, "-CAkey", self.cakey, "-set_serial", epoch, "-out", certpath], stdin=p1.stdout, stderr=PIPE)
                p2.communicate()

        self.send_response(200, 'Connection Established')
        self.end_headers()
        #self.wfile.flush() #need it?

        self.connection = ssl.wrap_socket(self.connection, keyfile=self.certkey, certfile=certpath, server_side=True, ssl_version=ssl.PROTOCOL_TLSv1_1)
        self.rfile = self.connection.makefile("rb", self.rbufsize)
        self.wfile = self.connection.makefile("wb", self.wbufsize)

        conntype = self.headers.get('Proxy-Connection', '')
        if self.protocol_version == "HTTP/1.1" and conntype.lower() != 'close':
            self.close_connection = 0
        else:
            self.close_connection = 1

    def connect_relay(self):
        address = self.path.split(':', 1)
        address[1] = int(address[1]) or 443
        try:
            s = socket.create_connection(address, timeout=self.timeout)
        except Exception as e:
            self.send_error(502)
            return
        self.send_response(200, 'Connection Established')
        self.end_headers()

        conns = [self.connection, s]
        self.close_connection = 0
        while not self.close_connection:
            rlist, wlist, xlist = select.select(conns, [], conns, self.timeout)
            if xlist or not rlist:
                break
            for r in rlist:
                other = conns[1] if r is conns[0] else conns[0]
                data = r.recv(8192)
                if not data:
                    self.close_connection = 1
                    break
                other.sendall(data)

    def make_req(self):
        req = self
        content_length = int(req.headers.get('Content-Length', 0))
        req_body = self.rfile.read(content_length) if content_length else ''

        if req.path[0] == '/':
            if isinstance(self.connection, ssl.SSLSocket):
                req.path = "https://%s%s" % (req.headers['Host'], req.path)
            else:
                req.path = "http://%s%s" % (req.headers['Host'], req.path)

        req_body_modified = self.request_handler(req, req_body)
        if req_body_modified is False:
            self.send_error(403)
            return
        elif req_body_modified is not None:
            req_body = req_body_modified
            req.headers['Content-length'] = str(len(req_body))

        u = urllib.parse.urlsplit(req.path)
        scheme, netloc, path = u.scheme, u.netloc, (u.path + '?' + u.query if u.query else u.path)
        assert scheme in ('http', 'https')
        if netloc:
            req.headers['Host'] = netloc
        setattr(req, 'headers', self.filter_headers(req.headers))
        try:
            origin = (scheme, netloc)
            if not origin in self.tls.conns:
                if scheme == 'https':
                    self.tls.conns[origin] = http.client.HTTPSConnection(netloc, timeout=self.timeout)
                else:
                    self.tls.conns[origin] = http.client.HTTPConnection(netloc, timeout=self.timeout)
            conn = self.tls.conns[origin]
            conn.request(self.command, path, req_body, dict(req.headers))
        except Exception as e:
            if origin in self.tls.conns:
                del self.tls.conns[origin]
            self.send_error(502)
        return (req, req_body, conn)

    def make_ownreq(self):
        req = self.request
        content_length = int(req.headers.get('Content-Length', 0))
        req_body = self.rfile.read(content_length) if content_length else ''
        if req.path[0] == '/':
            if isinstance(self.connection, ssl.SSLSocket):
                req.path = "https://%s%s" % (req.headers['Host'], req.path)
            else:
                req.path = "http://%s%s" % (req.headers['Host'], req.path)
        u = urllib.parse.urlsplit(req.path)
        scheme, netloc, path = u.scheme, u.netloc, (u.path + '?' + u.query if u.query else u.path)
        assert scheme in ('http', 'https')
        if netloc:
            req.headers['Host'] = netloc
        setattr(req, 'headers', self.filter_headers(self, req.headers))
        origin = (scheme, netloc)
        if scheme == 'https':
            conns = http.client.HTTPSConnection(netloc, timeout=self.timeout)
        else:
            conns = http.client.HTTPConnection(netloc, timeout=self.timeout)
        conn = conns
        conn.request(req.command, path, req_body, dict(req.headers))
        return req_body, conn


    def make_res(self, req_body, conn):
        res = conn.getresponse()

        version_table = {10: 'HTTP/1.0', 11: 'HTTP/1.1'}
        setattr(res, 'headers', res.msg)
        setattr(res, 'response_version', version_table[res.version])

        # support streaming
        if not 'Content-Length' in res.headers and 'no-store' in res.headers.get('Cache-Control', ''):
            self.response_handler(req_body, res, '')
            setattr(res, 'headers', self.filter_headers(res.headers))
            self.relay_streaming(res)
            with self.lock:
                self.save_handler(self, req_body, res, '')
            return res, '', ''

        res_body = res.read()
        content_encoding = res.headers.get('Content-Encoding', 'identity')
        if res_body is not None:
            res_body_plain = self.decode_content_body(res_body, content_encoding)
        else:
            res_body_plain = ''
        #res_body_modified = self.response_handler(req_body, res, res_body_plain)
        #if res_body_modified is False:
        #    self.send_error(403)
        #    return
        #elif res_body_modified is not None:
        #    res_body_plain = res_body_modified
        #    res_body = self.encode_content_body(res_body_plain, content_encoding)
        #    res.headers['Content-Length'] = str(len(res_body))

        setattr(res, 'headers', self.filter_headers(res.headers))
        self.send_response(res.status, res.reason)
        for line in res.headers.items():
            self.send_header(line[0], line[1])
        self.end_headers()
        if(res_body != b''):
            self.wfile.write(res_body)
        self.wfile.flush()
        return res, res_body, res_body_plain

    def make_ownres(self, req_body, conn):
        res = conn.getresponse()

        version_table = {10: 'HTTP/1.0', 11: 'HTTP/1.1'}
        setattr(res, 'headers', res.msg)
        setattr(res, 'response_version', version_table[res.version])

        # support streaming
        if not 'Content-Length' in res.headers and 'no-store' in res.headers.get('Cache-Control', ''):
            self.response_handler(req_body, res, '')
            setattr(res, 'headers', self.filter_headers(self, res.headers))
            self.relay_streaming(res)
            with self.lock:
                self.save_handler(self, req_body, res, '')
            return

        res_body = res.read()
        content_encoding = res.headers.get('Content-Encoding', 'identity')
        res_body_plain = self.decode_content_body(self, res_body, content_encoding)

        setattr(res, 'headers', self.filter_headers(self, res.headers))
        return res, res_body, res_body_plain

    def do_GET(self):
        global is_modified
        if is_modified == False:
            if self.mode == 'Sniffing':
                req, req_body, conn = self.make_req()
                res, res_body, res_body_plain = self.make_res(req_body, conn)
                with self.lock:
                    self.save_handler(req, req_body, res, res_body_plain)

                if self.mode == 'Intercepting':
                    if (input('Premi invio per continuare') == 'sniffing'):
                        self.mode = 'Sniffing'
                    else:
                        req, req_body, conn = self.make_req()
                        self.print_request(req, req_body)
                        res, res_body, res_body_plain = self.make_res(req_body, conn)
                        self.print_response(res, res_body_plain)

        if is_modified == True:

            req_body, conn = self.make_ownreq(self)
            res, res_body, res_body_plain = self.make_ownres(self, req_body, conn)
            self.print_response(self, res, res_body_plain)
            is_modified = False

    def relay_streaming(self, res):
        self.send_response(res.status, res.reason)
        for line in res.headers.items():
            self.send_header(line[0],line[1])
        self.end_headers()
        try:
            while True:
                chunk = res.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
            self.wfile.flush()
        except socket.error:
            # connection closed by client
            pass

    do_HEAD = do_GET
    do_POST = do_GET
    do_PUT = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET

    def filter_headers(self, headers):
        # http://tools.ietf.org/html/rfc2616#section-13.5.1
        hop_by_hop = ('connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade')
        for k in hop_by_hop:
            del headers[k]

        # accept only supported encodings
        if 'Accept-Encoding' in headers:
            ae = headers['Accept-Encoding']
            filtered_encodings = [x for x in re.split(r',\s*', ae) if x in ('identity', 'gzip', 'x-gzip', 'deflate' 'br')]
            headers['Accept-Encoding'] = ', '.join(filtered_encodings)

        return headers

    def encode_content_body(self, text, encoding):
        if encoding == 'identity':
            data = text
        elif encoding in ('gzip', 'x-gzip'):
            io = BytesIO()
            with gzip.GzipFile(fileobj=io, mode='wb') as f:
                f.write(text)
            data = io.getvalue()
        elif encoding == 'deflate':
            data = zlib.compress(text)
        elif encoding == 'br':
            data = brotli.compress(text)
        else:
            raise Exception("Unknown Content-Encoding: %s" % encoding)
        return data

    def decode_content_body(self, data, encoding):
        if encoding == 'identity':
            text = data
        elif encoding in ('gzip', 'x-gzip'):
            io = BytesIO(data)
            with gzip.GzipFile(fileobj=io) as f:
                text = f.read()
        elif encoding == 'deflate':
            try:
                text = zlib.decompress(data)
            except zlib.error:
                text = zlib.decompress(data, -zlib.MAX_WBITS)
        elif encoding == 'br':
            text = brotli.decompress(data)
        else:
            raise Exception("Unknown Content-Encoding: %s" % encoding)
        return text

    def send_cacert(self):
        with open(self.cacert, 'rb') as f:
            data = f.read()
        msg2 = "%s %d %s\r\n" % (self.protocol_version, 200, 'OK')
        self.wfile.write(msg2.encode('utf-8'))
        self.send_header('Content-Type', 'application/x-x509-ca-cert')
        self.send_header('Content-Length', len(data))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)

    def save_info(self, req, req_body, res, res_body):
        def parse_qsl(s):
            return '\n'.join("%-20s %s" % (k, v) for k, v in urllib.parse.parse_qsl(s, keep_blank_values=True))
        query_text = ""
        u = urllib.parse.urlsplit(req.path)
        if u.query:
            query_text = parse_qsl(u.query)

        cookie = req.headers.get('Cookie', '')
        if cookie:
            cookie = parse_qsl((re.sub(r';\s*', '&', cookie)))

        auth = req.headers.get('Authorization', '')
        if auth.lower().startswith('basic'):
            token = auth.split()[1].decode('base64')

        if req_body is not '':
            req_body_text = None
            content_type = req.headers.get('Content-Type', '')

            if content_type.startswith('application/x-www-form-urlencoded'):
                #req_body_text = parse_qsl(req_body.decode('utf-8'))
                req_body_text = req_body.decode('utf-8')
            elif content_type.startswith('application/json'):
                try:
                    json_obj = json.loads(req_body.decode())
                    json_str = json.dumps(json_obj, indent=2)
                    if json_str.count('\n') < 50:
                        req_body_text = json_str
                    else:
                        lines = json_str.splitlines()
                        req_body_text = "%s\n(%d lines)" % ('\n'.join(lines[:50]), len(lines))
                except ValueError:
                    req_body_text = req_body
            elif len(req_body) < 1024:
                req_body_text = req_body.decode()
        else:
            req_body_text = ''

        self.reqlist.append(Request((len(self.reqlist) + 1), req.command, req.headers.get('Host'), req.path, req.request_version, req.headers, query_text, cookie, req_body_text))

        cookies = res.headers.get('Set-Cookie')
        res_body_text = ''
        if res_body is not '':
            content_type = res.headers.get('Content-Type', '')
            try:
                if content_type.startswith('application/json'):
                    try:
                        if(isinstance(res_body, str)):
                            json_obj = json.loads(res_body)
                            json_str = json.dumps(json_obj, indent=2)
                        else:
                            json_obj = json.loads(res_body.decode())
                            json_str = json.dumps(json_obj, indent=2)
                        if json_str.count('\n') < 50:
                            res_body_text = json_str
                        else:
                            lines = json_str.splitlines()
                            res_body_text = "%s\n(%d lines)" % ('\n'.join(lines[:50]), len(lines))
                    except ValueError:
                        res_body_text = res_body
                elif content_type.startswith('text/'):
                    if(isinstance(res_body, str)):
                        res_body_text = res_body
                    else:
                        res_body_text = res_body.decode()
            except ValueError:
                #print(str(res_body) + '\n\n\n')
                res_body_text = str(res_body)

        self.reslist.append(Response((len(self.reslist) + 1), res.response_version, res.status, res.reason, res.headers, cookies, res_body_text))

    def print_info(self, req, req_body, res, res_body):
        def parse_qsl(s):
            return '\n'.join("%-20s %s" % (k, v) for k, v in urllib.parse.parse_qsl(s, keep_blank_values=True))

        req_header_text = "%s %s %s\n%s" % (req.command, req.path, req.request_version, req.headers)
        res_header_text = "%s %d %s\n%s" % (res.response_version, res.status, res.reason, res.headers)

        print(yellow(req_header_text))

        u = urllib.parse.urlsplit(req.path)
        if u.query:
            query_text = parse_qsl(u.query)
            print(yellow( "==== QUERY PARAMETERS ====\n%s\n" % query_text))

        cookie = req.headers.get('Cookie', '')
        if cookie:
            cookie = parse_qsl((re.sub(r';\s*', '&', cookie)))
            print(yellow("==== COOKIE ====\n%s\n" % cookie))

        auth = req.headers.get('Authorization', '')
        if auth.lower().startswith('basic'):
            token = auth.split()[1].decode('base64')
            print(red("==== BASIC AUTH ====\n%s\n" % token))

        if req_body is not None:
            req_body_text = None
            content_type = req.headers.get('Content-Type', '')

            if content_type.startswith('application/x-www-form-urlencoded'):
                req_body_text = parse_qsl(req_body.decode('utf-8'))
            elif content_type.startswith('application/json'):
                try:
                    json_obj = json.loads(req_body.decode())
                    json_str = json.dumps(json_obj, indent=2)
                    if json_str.count('\n') < 50:
                        req_body_text = json_str
                    else:
                        lines = json_str.splitlines()
                        req_body_text = "%s\n(%d lines)" % ('\n'.join(lines[:50]), len(lines))
                except ValueError:
                    req_body_text = req_body
            elif len(req_body) < 1024:
                req_body_text = req_body.decode()
            if req_body_text:
                print(yellow("==== REQUEST BODY ====\n%s\n" % req_body_text))

        print(cyan(res_header_text))

        cookies = res.headers.get('Set-Cookie')
        if cookies:
            #cookies = '\n'.join(cookies)
            print(red("==== SET-COOKIE ====\n%s\n" % cookies))

        if res_body is not None:
            res_body_text = None
            content_type = res.headers.get('Content-Type', '')

            if content_type.startswith('application/json'):
                try:
                    json_obj = json.loads(res_body.decode())
                    json_str = json.dumps(json_obj, indent=2)
                    if json_str.count('\n') < 50:
                        res_body_text = json_str
                    else:
                        lines = json_str.splitlines()
                        res_body_text = "%s\n(%d lines)" % ('\n'.join(lines[:50]), len(lines))
                except ValueError:
                    res_body_text = res_body
            elif content_type.startswith('text/html'):
                m = re.search(r'<title[^>]*>\s*([^<]+?)\s*</title>', res_body.decode(), re.I)
                if m:
                    h = HTMLParser()
                    print(cyan("==== HTML TITLE ====\n%s\n" % h.unescape(m.group(1))))
            elif content_type.startswith('text/') and len(res_body) < 1024:
                res_body_text = res_body.decode()

            if res_body_text: #Se tolgo questa condizione stampa tutto il codice html
                print(cyan("==== RESPONSE BODY ====\n%s\n" % res_body_text))

    def print_request(self, req, req_body):
        def parse_qsl(s):
            return '\n'.join("%-20s %s" % (k, v) for k, v in urllib.parse.parse_qsl(s, keep_blank_values=True))

        req_header_text = "%s %s %s\n%s" % (req.command, req.path, req.request_version, req.headers)

        print(yellow(req_header_text))

        u = urllib.parse.urlsplit(req.path)
        if u.query:
            query_text = parse_qsl(u.query)
            print(yellow("==== QUERY PARAMETERS ====\n%s\n" % query_text))

        cookie = req.headers.get('Cookie', '')
        if cookie:
            cookie = parse_qsl((re.sub(r';\s*', '&', cookie)))
            print(yellow("==== COOKIE ====\n%s\n" % cookie))

        auth = req.headers.get('Authorization', '')
        if auth.lower().startswith('basic'):
            token = auth.split()[1].decode('base64')
            print(yellow("==== BASIC AUTH ====\n%s\n" % token))

        if req_body is not None:
            req_body_text = None
            content_type = req.headers.get('Content-Type', '')

            if content_type.startswith('application/x-www-form-urlencoded'):
                req_body_text = parse_qsl(req_body.decode('utf-8'))
            elif content_type.startswith('application/json'):
                try:
                    json_obj = json.loads(req_body.decode())
                    json_str = json.dumps(json_obj, indent=2)
                    if json_str.count('\n') < 50:
                        req_body_text = json_str
                    else:
                        lines = json_str.splitlines()
                        req_body_text = "%s\n(%d lines)" % ('\n'.join(lines[:50]), len(lines))
                except ValueError:
                    req_body_text = req_body.decode()
            elif len(req_body) < 1024:
                req_body_text = req_body.decode()
            if req_body_text:
                print(yellow("==== REQUEST BODY ====\n%s\n" % req_body_text))


    def print_response(self, res, res_body):
        res_header_text = "%s %d %s\n%s" % (res.response_version, res.status, res.reason, res.headers)
        print(cyan(res_header_text))
        cookies = res.headers.get('Set-Cookie')
        res_body_text = ' '
        if cookies:
            # cookies = '\n'.join(cookies)
            print(cyan("==== SET-COOKIE ====\n%s\n" % cookies))

        if res_body is not None:
            content_type = res.headers.get('Content-Type', '')

            if content_type.startswith('application/json'):
                try:
                    json_obj = json.loads(res_body.decode())
                    json_str = json.dumps(json_obj, indent=2)
                    if json_str.count('\n') < 50:
                        res_body_text = json_str
                    else:
                        lines = json_str.splitlines()
                        res_body_text = "%s\n(%d lines)" % ('\n'.join(lines[:50]), len(lines))
                except ValueError:
                    res_body_text = res_body.decode()
            elif content_type.startswith('text/'):
                res_body_text = res_body.decode()
        if res_body_text != '':
            print(cyan(res_body_text))

    def request_handler(self, req, req_body):
        #req.command = "POST"
        pass

    def response_handler(self, req_body, res, res_body):
        pass

    def save_handler(self, req, req_body, res, res_body):
        #self.print_info(req, req_body, res, res_body)
        self.save_info(req, req_body,res, res_body)


class Proxy:
    def __init__(self):
        self.HandlerClass = ProxyRequestHandler
        self.ServerClass = ThreadingHTTPServer
        self.protocol = "HTTP/1.1"
        self.httpd = None

    def start(self):
        if sys.argv[1:]:
            port = int(sys.argv[1])
        else:
            port = 8080
        server_address = ('localhost', port)

        self.HandlerClass.protocol_version = self.protocol
        self.httpd = self.ServerClass(server_address, self.HandlerClass)
        self.httpd.serve_forever()

    def start_intercept(self):
        self.HandlerClass.mode = 'Intercepting'

    def start_sniffing(self):
        self.HandlerClass.mode = 'Sniffing'

    def get_srequest(self):
        return self.HandlerClass

    def modify(self, request1):
        self.HandlerClass.request = request1
        global is_modified
        is_modified = True
        self.HandlerClass.do_GET(self.HandlerClass)

        #self.HandlerClass.request_handler(self, req, req_body)

    def close(self):
        self.httpd.shutdown()

    def get_req(self):
        return self.HandlerClass.reqlist

    def get_res(self):
        return self.HandlerClass.reslist


if __name__ == '__main__':
    Proxy = Proxy()
    Proxy.start()

