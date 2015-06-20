# -- coding: utf-8 --

from urlparse import urlparse, parse_qsl, urljoin
import urllib
import string
import random
import logging
import re

from scrapy.contrib.linkextractors import LinkExtractor

from scrapy.contrib.spiders import CrawlSpider, Rule
from scrapy.http import FormRequest, Request
from scrapy.http.cookies import CookieJar
import lxml.etree
import lxml.html

from ignore import IgnoreHandler
from xsscrapy.items import inj_resp
from xsscrapy.loginform import fill_login_form

# from IPython import embed

__author__ = 'Dan McInerney danhmcinerney@gmail.com'


class XSSspider(CrawlSpider):
    name = 'xsscrapy'
    logging.basicConfig(filename='xsscrapy.log', level=logging.DEBUG)

    # If you're logging into a site with a logout link, you'll want to
    # uncomment the rule below and comment the shorter one right after to
    # prevent yourself from being logged out automatically
    rules = (Rule(LinkExtractor(), callback='parse_resp', follow=True), )

    def __init__(self, *args, **kwargs):
        # run using: scrapy crawl xss_spider -a url='http://example.com'
        super(XSSspider, self).__init__(*args, **kwargs)
        self.start_urls = [kwargs.get('url')]
        hostname = urlparse(self.start_urls[0]).hostname
        # With subdomains
        allow_domains = [hostname]
        if kwargs.get('domain') != 'None':
            allow_domains = [kwargs.get('domain')]
        self.allowed_domains = allow_domains  # adding [] around the value seems to allow it to crawl subdomain of value
        self.delim = '1zqjx'
        # semi colon goes on end because sometimes it cuts stuff off like
        # gruyere or the second cookie delim
        self.test_str = '\'"(){}<x>:/'

        # Login details
        self.login_user = kwargs.get('user')
        if self.login_user == 'None':
            self.login_user = None
        else:
            # Don't hit links with 'logout' in them since self.login_user exists
            self.rules = (Rule(LinkExtractor(deny=('logout')), callback='parse_resp', follow=True), )
        if kwargs.get('pw') == 'None' and self.login_user is not None:
            self.login_pass = raw_input("Please enter the password: ")
        else:
            self.login_pass = kwargs.get('pw')

        # HTTP Basic Auth
        self.basic_auth = kwargs.get('basic')
        if self.basic_auth == 'true':
            self.http_user = self.login_user
            self.http_pass = self.login_pass

        self.cookie = kwargs.get('cookie')

        self.cur_cookie = self.parse_raw_cookie()
        handler = IgnoreHandler()
        self.handler = handler
        handler.start_watch()


    def parse_start_url(self, response):
        ''' Creates the XSS tester requests for the start URL as well as the request for robots.txt '''
        u = urlparse(response.url)
        self.base_url = u.scheme + '://' + u.netloc
        robots_url = self.base_url + '/robots.txt'
        robot_req = [Request(robots_url, callback=self.robot_parser)]

        reqs = self.parse_resp(response)
        reqs += robot_req
        return self.filter_ignore(reqs)

    def filter_ignore(self, requests):
        ignore_start = self.handler.start_with
        ignore_pat = self.handler.pat
        filtered = []
        for req in requests:
            url = req.url
            mt = False
            for st in ignore_start:
                if url.startswith(st):
                    mt = True
            if not mt:
                filtered.append(req)
        requests = filtered
        filtered = []
        for req in requests:
            url = req.url
            mt = False
            for st in ignore_pat:
                if re.compile(st).match(url):
                    mt = True
            if not mt:
                filtered.append(req)
        return filtered
        pass

    def is_ignored(self, url):
        ignore_start = self.handler.start_with
        for st in ignore_start:
                if url.startswith(st):
                    return True
        ignore_pat = self.handler.pat
        for st in ignore_pat:
                if re.compile(st).match(url):
                    return True
        return False
        pass

    #### Handle logging in if username and password are given as arguments ####
    def start_requests(self):
        ''' If user and pw args are given, pass the first response to the login handler
            otherwise pass it to the normal callback function '''
        if self.login_user and self.login_pass:
            if self.basic_auth == 'true':
                yield Request(
                    url=self.start_urls[0])  # Take out the callback arg so crawler falls back to the rules' callback
            else:
                yield Request(url=self.start_urls[0], callback=self.login)
        else:
            if self.cur_cookie is None:
                yield Request(
                    url=self.start_urls[0])  # Take out the callback arg so crawler falls back to the rules' callback
            else:
                yield Request(url=self.start_urls[0], cookies=self.parse_raw_cookie())

    def parse_raw_cookie(self, raw_cookie=None):
        if raw_cookie is None:
            raw_cookie = self.cookie
        if raw_cookie is None:
            return None
        dct = {}
        kv = raw_cookie.split(';')
        for i in kv:
            pt = i.split('=')
            dct[pt[0]] = pt[1]
        return dct
        pass

    def parse_set_cookie(self, set_cookie):
        if set_cookie is None:
            return None
        dct = {}
        kv = set_cookie.split(';')
        for i in kv:
            pt = i.split('=')
            dct[pt[0]] = pt[1]
            break
        return dct

    def login(self, response):
        ''' Fill out the login form and return the request'''
        self.log('Logging in...')
        try:
            args, url, method = fill_login_form(response.url, response.body, self.login_user, self.login_pass)
            return FormRequest(url,
                               method=method,
                               formdata=args,
                               callback=self.confirm_login,
                               dont_filter=True)

        except Exception:
            self.log('Login failed')  # Make this more specific eventually
            return Request(url=self.start_urls[0], dont_filter=True)  # Continue crawling

    def confirm_login(self, response):
        ''' Check that the username showed up in the response page '''
        if self.login_user.lower() in response.body.lower():
            self.log('Successfully logged in (or, at least, the username showed up in the response html)')
            return Request(url=self.start_urls[0], dont_filter=True)
        else:
            self.log('FAILED to log in! (or at least cannot find the username on the post-login page which may be OK)')
            return Request(url=self.start_urls[0], dont_filter=True)

    ###########################################################################

    def robot_parser(self, response):
        ''' Parse the robots.txt file and create Requests for the disallowed domains '''
        disallowed_urls = set([])
        for line in response.body.splitlines():
            if 'disallow: ' in line.lower():
                try:
                    address = line.split()[1]
                except IndexError:
                    # In case Disallow: has no value after it
                    continue
                disallowed = self.base_url + address
                disallowed_urls.add(disallowed)

        reqs = []
        for u in disallowed_urls:
            if u != self.base_url:
                if self.cur_cookie is None:
                    reqs.append(Request(u, callback=self.parse_resp))
                else:
                    reqs.append(Request(u, cookies=self.parse_raw_cookie(), callback=self.parse_resp))

        for r in reqs:
            self.log('Added robots.txt disallowed URL to our queue: ' + r.url)
        return reqs

    def parse_resp(self, response):
        ''' The main response parsing function, called on every response from a new URL
        Checks for XSS in headers and url'''
        reqs = []
        orig_url = response.url
        body = response.body
        if self.is_ignored(orig_url):
            return reqs
        logging.warning(orig_url)

        set_cookie = ""
        try:
            set_cookie = response.headers["Set-Cookie"]
        except:
            pass

        if set_cookie != "":
            self.cur_cookie.update(self.parse_set_cookie(set_cookie))

        try:
            # soupparser will handle broken HTML better (like identical attributes) but god damn will you pay for it
            # in CPU cycles. Slows the script to a crawl and introduces more bugs.
            doc = lxml.html.fromstring(body, base_url=orig_url)
        except lxml.etree.ParserError:
            self.log('ParserError from lxml on %s' % orig_url)
            return
        except lxml.etree.XMLSyntaxError:
            self.log('XMLSyntaxError from lxml on %s' % orig_url)
            return

        forms = doc.xpath('//form')
        payload = self.test_str

        # Grab iframe source urls if they are part of the start_url page
        iframe_reqs = self.make_iframe_reqs(doc, orig_url)
        if iframe_reqs:
            reqs += iframe_reqs

        # Edit a few select headers with injection string and resend request
        # Left room to add more header injections too
        test_headers = []
        test_headers.append('Referer')
        if 'UA' in response.meta:
            if response.meta['UA'] in body:
                test_headers.append('User-Agent')
        header_reqs = self.make_header_reqs(orig_url, payload, test_headers)
        if header_reqs:
            reqs += header_reqs

        # Edit the cookies; easier to do this in separate function from make_header_reqs()
        cookie_reqs = self.make_cookie_reqs(orig_url, payload, 'cookie')
        if cookie_reqs:
            reqs += cookie_reqs

        # Fill out forms with xss strings
        if forms:
            form_reqs = self.make_form_reqs(orig_url, forms, payload)
            if form_reqs:
                reqs += form_reqs

        # Test URL variables with xss strings
        payloaded_urls, url_delim_str = self.make_URLs(orig_url,
                                                       payload)  # list of tuples where item[0]=url, and item[1]=changed param
        print 'URL:', payloaded_urls, url_delim_str
        if payloaded_urls:
            url_reqs = self.make_url_reqs(orig_url, payloaded_urls, url_delim_str)
            if url_reqs:
                reqs += url_reqs

        # Add the original untampered response to each request for use by sqli_check()
        for r in reqs:
            r.meta['orig_body'] = body

        # Each Request here will be given a specific callback relative to whether it was URL variables or form inputs that were XSS payloaded
        return self.filter_ignore(reqs)

    def url_valid(self, url, orig_url):
        # Make sure there's a form action url
        if url == None:
            self.log('No form action URL found')
            return

        # Sometimes lxml doesn't read the form.action right
        if '://' not in url:
            self.log('Form URL contains no scheme, attempting to put together a working form submissions URL')
            proc_url = self.url_processor(orig_url)
            url = proc_url[1] + proc_url[0] + url

        return url

    def make_iframe_reqs(self, doc, orig_url):
        ''' Grab the <iframe src=...> attribute and add those URLs to the
        queue should they be within the start_url domain '''

        parsed_url = urlparse(orig_url)
        iframe_reqs = []
        iframes = doc.xpath('//iframe/@src')
        frames = doc.xpath('//frame/@src')

        all_frames = iframes + frames

        url = None
        for i in all_frames:
            if type(i) == unicode:
                i = str(i).strip()
            # Nonrelative path
            if '://' in i:
                # Skip iframes to outside sources
                try:
                    if self.base_url in i[:len(self.base_url) + 1]:
                        url = i
                except IndexError:
                    continue
            # Relative path
            else:
                url = urljoin(orig_url, i)

            if url:
                if self.cur_cookie is None:
                    iframe_reqs.append(Request(url))
                else:
                    iframe_reqs.append(Request(url, cookies=self.parse_raw_cookie()))

        if len(iframe_reqs) > 0:
            return iframe_reqs

    def make_form_reqs(self, orig_url, forms, payload):
        ''' Payload each form input in each input's own request '''
        reqs = []
        vals_urls_meths = []

        two_rand_letters = random.choice(string.lowercase) + random.choice(string.lowercase)
        delim_str = self.delim + two_rand_letters
        payload = delim_str + payload + delim_str + ';9'

        for form in forms:
            if form.inputs:
                method = form.method
                form_url = form.action or form.base_url
                url = self.url_valid(form_url, orig_url)
                if url and method:
                    for i in form.inputs:
                        if i.name:
                            if type(i).__name__ not in ['InputElement', 'TextareaElement']:
                                continue
                            if type(i).__name__ == 'InputElement':
                                # Don't change values for the below types because they
                                # won't be strings and lxml will complain
                                nonstrings = ['checkbox', 'radio', 'submit']
                                if i.type in nonstrings:
                                    continue
                            orig_val = form.fields[i.name]
                            if orig_val == None:
                                orig_val = ''
                            # Foriegn languages might cause this like russian "yaca" for "checkbox"
                            try:
                                form.fields[i.name] = payload
                            except ValueError as e:
                                self.log('Error: ' + str(e))
                                continue
                            xss_param = i.name
                            values = form.form_values()
                            if self.cur_cookie is None:
                                req = FormRequest(url,
                                                  formdata=values,
                                                  method=method,
                                                  meta={'payload': payload,
                                                        'xss_param': xss_param,
                                                        'orig_url': orig_url,
                                                        'xss_place': 'form',
                                                        'POST_to': url,
                                                        'delim': delim_str},
                                                  dont_filter=True,
                                                  callback=self.xss_chars_finder)
                            else:
                                req = FormRequest(url,
                                                  formdata=values,
                                                  method=method,
                                                  meta={'payload': payload,
                                                        'xss_param': xss_param,
                                                        'orig_url': orig_url,
                                                        'xss_place': 'form',
                                                        'POST_to': url,
                                                        'delim': delim_str},
                                                  dont_filter=True,
                                                  cookies=self.parse_raw_cookie(),
                                                  callback=self.xss_chars_finder)
                            reqs.append(req)
                            # Reset the value
                            try:
                                form.fields[i.name] = orig_val
                            except ValueError as e:
                                self.log('Error: ' + str(e))
                                continue

        if len(reqs) > 0:
            return reqs

    def make_cookie_reqs(self, url, payload, xss_param):
        ''' Generate payloaded cookie header requests '''

        two_rand_letters = random.choice(string.lowercase) + random.choice(string.lowercase)
        delim_str = self.delim + two_rand_letters
        payload = delim_str + payload + delim_str + ';9'

        cks = {'userinput': payload}
        if self.cur_cookie is not None:
            cks.update(self.parse_raw_cookie())
        reqs = [Request(url,
                        meta={'xss_place': 'header',
                              'cookiejar': CookieJar(),
                              'xss_param': xss_param,
                              'orig_url': url,
                              'payload': payload,
                              'delim': delim_str},
                        cookies=cks,
                        callback=self.xss_chars_finder,
                        dont_filter=True)]

        if len(reqs) > 0:
            return reqs

    def make_URLs(self, url, payload):
        ''' Add links with variables in them to the queue again but with XSS testing payloads 
        Will return a tuple: (url, injection point, payload) '''

        two_rand_letters = random.choice(string.lowercase) + random.choice(string.lowercase)
        delim_str = self.delim + two_rand_letters
        payload = delim_str + payload + delim_str + ';9'

        if '=' in url and '?' in url:
            # If URL has variables, payload them
            payloaded_urls = self.payload_url_vars(url, payload)
        else:
            # If URL has no variables, tack payload onto end of URL
            payloaded_urls = self.payload_end_of_url(url, payload)

        return payloaded_urls, delim_str

    def payload_end_of_url(self, url, payload):
        ''' Payload the end of the URL to catch some DOM(?) and other reflected XSSes '''

        # Make URL test and delim strings unique
        if url[-1] == '/':
            payloaded_url = url + payload
        else:
            payloaded_url = url + '/' + payload

        return [(payloaded_url, 'end of url', payload)]

    def payload_url_vars(self, url, payload):
        ''' Payload the URL variables '''
        payloaded_urls = []
        params = self.getURLparams(url)
        modded_params = self.change_params(params, payload)
        netloc, protocol, doc_domain, path = self.url_processor(url)
        if netloc and protocol and path:
            for payload in modded_params:
                for params in modded_params[payload]:
                    joinedParams = urllib.urlencode(params, doseq=1)  # doseq maps the params back together
                    newURL = urllib.unquote(protocol + netloc + path + '?' + joinedParams)

                    # Prevent nonpayloaded URLs
                    if self.test_str not in newURL:
                        continue

                    for p in params:
                        if payload in p[1]:
                            changed_value = p[0]

                    payloaded_urls.append((newURL, changed_value, payload))

        # Payload the path, like: example.com/page1.php?param=val becomes example.com/page1.php/FUZZCHARS/?param=val
        # payloaded_urls.append(self.payload_path(url))

        if len(payloaded_urls) > 0:
            return payloaded_urls

    def payload_path(self, url):
        ''' Payload the path, like: example.com/page1.php?param=val becomes example.com/page1.php/FUZZCHARS/?param=val '''
        return urlparse(url)


    def getURLparams(self, url):
        ''' Parse out the URL parameters '''
        parsedUrl = urlparse(url)
        fullParams = parsedUrl.query
        # parse_qsl rather than parse_ps in order to preserve order
        params = parse_qsl(fullParams, keep_blank_values=True)
        return params

    def change_params(self, params, payload):
        ''' Returns a list of complete parameters, each with 1 parameter changed to an XSS vector '''
        changedParams = []
        changedParam = False
        moddedParams = []
        allModdedParams = {}

        # Create a list of lists, each list will be the URL we will test
        # This preserves the order of the URL parameters and will also
        # test each parameter individually instead of all at once
        allModdedParams[payload] = []
        for x in xrange(0, len(params)):
            for p in params:
                param = p[0]
                value = p[1]
                # If a parameter has not been modified yet
                if param not in changedParams and changedParam == False:
                    changedParams.append(param)
                    p = (param, value + payload)
                    moddedParams.append(p)
                    changedParam = param
                else:
                    moddedParams.append(p)

            # Reset so we can step through again and change a diff param
            # allModdedParams[payload].append(moddedParams)
            allModdedParams[payload].append(moddedParams)

            changedParam = False
            moddedParams = []

        # Reset the list of changed params each time a new payload is attempted
        # changedParams = []

        if len(allModdedParams) > 0:
            return allModdedParams

    def url_processor(self, url):
        ''' Get the url domain, protocol, and netloc using urlparse '''
        try:
            parsed_url = urlparse(url)
            # Get the path
            path = parsed_url.path
            # Get the protocol
            protocol = parsed_url.scheme + '://'
            # Get the hostname (includes subdomains)
            hostname = parsed_url.hostname
            # Get netloc (domain.com:8080)
            netloc = parsed_url.netloc
            # Get doc domain
            doc_domain = '.'.join(hostname.split('.')[-2:])
        except:
            self.log('Could not parse url: ' + url)
            return

        return (netloc, protocol, doc_domain, path)

    def make_url_reqs(self, orig_url, payloaded_urls, delim_str):
        ''' Make the URL requests '''

        reqs = []
        for url in payloaded_urls:
            if self.cur_cookie is None:
                reqs.append(Request(url[0],
                                    meta={'xss_place': 'url',
                                          'xss_param': url[1],
                                          'orig_url': orig_url,
                                          'payload': url[2],
                                          'delim': delim_str},
                                    callback=self.xss_chars_finder))
            else:
                reqs.append(Request(url[0],
                                    meta={'xss_place': 'url',
                                          'xss_param': url[1],
                                          'orig_url': orig_url,
                                          'payload': url[2],
                                          'delim': delim_str},
                                    cookies=self.parse_raw_cookie(),
                                    callback=self.xss_chars_finder))

        if len(reqs) > 0:
            return reqs

    def make_header_reqs(self, url, payload, inj_headers):
        ''' Generate header requests '''

        two_rand_letters = random.choice(string.lowercase) + random.choice(string.lowercase)
        delim_str = self.delim + two_rand_letters
        payload = delim_str + payload + delim_str + ';9'

        reqs = []
        for inj_header in inj_headers:
            if self.cur_cookie is None:
                reqs.append(Request(url,
                                    headers={inj_header: payload},
                                    meta={'xss_place': 'header',
                                          'xss_param': inj_header,
                                          'orig_url': url,
                                          'payload': payload,
                                          'delim': delim_str,
                                          'UA': self.get_user_agent(inj_header, payload)},
                                    dont_filter=True,
                                    callback=self.xss_chars_finder))
            else:
                reqs.append(Request(url,
                                    headers={inj_header: payload},
                                    meta={'xss_place': 'header',
                                          'xss_param': inj_header,
                                          'orig_url': url,
                                          'payload': payload,
                                          'delim': delim_str,
                                          'UA': self.get_user_agent(inj_header, payload)},
                                    dont_filter=True,
                                    cookies=self.parse_raw_cookie(),
                                    callback=self.xss_chars_finder))

        if len(reqs) > 0:
            return reqs

    def get_user_agent(self, header, payload):
        if header == 'User-Agent':
            return payload
        else:
            return ''

    def xss_chars_finder(self, response):
        ''' Find which chars, if any, are filtered '''

        item = inj_resp()
        item['resp'] = response
        return item
