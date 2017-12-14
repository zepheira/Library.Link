'''
'''

import re
import sys
import logging
import urllib
import urllib.request
from itertools import *
import collections.abc

import requests

#from versa.writer.rdfs import prep as statement_prep
from versa.driver import memory
from versa import I, VERSA_BASEIRI, ORIGIN, RELATIONSHIP, TARGET, ATTRIBUTES
from versa.reader import rdfalite
from versa.reader.rdfalite import RDF_NS, SCHEMAORG_NS
from versa import util

from bibframe import BFZ, BL
from bibframe.zextra import LL

from rdflib import URIRef, Literal
from rdflib import BNode

from amara3 import iri
from amara3.uxml import tree
from amara3.uxml import xml
from amara3.uxml.treeutil import *
from amara3.uxml import html5

RDFTYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'
SCHEMAORG = 'http://schema.org/'


def all_sites(sitemap_url='http://library.link/harvest/sitemap.xml'):
    '''
    >>> from librarylink.util import all_sites
    >>> [ s.host for s in all_sites() if 'denverlibrary' in s.host ]
    ['link.denverlibrary.org']
    '''
    #FIXME: Avoid accumulating all the nodes, which will require improvements to xml.treesequence
    @coroutine
    def sink(accumulator):
        while True:
            e = yield
            loc = next(select_name(e, 'loc'))
            lastmod = next(select_name(e, 'lastmod'))
            s = liblink_site()
            s.sitemap = loc.xml_value
            s.base_url, _, tail = s.sitemap.partition('harvest/sitemap.xml')
            #Early warning for funky URLs breaking stuff downstream
            assert not tail
            protocol, s.host, path, query, fragment = iri.split_uri_ref(s.sitemap)
            s.lastmod = lastmod.xml_value
            accumulator.append(s)

    nodes = []
    ts = xml.treesequence(('sitemapindex', 'sitemap'), sink(nodes))
    if hasattr (all_sites, 'cachedir'):
        sess = CacheControl(requests.Session(), cache=FileCache(all_sites.cachedir))
    else:
        sess = CacheControl(requests.Session())
    result = sess.get(sitemap_url)
    ts.parse(result.text)
    yield from nodes

try:
    from cachecontrol import CacheControl
    from cachecontrol.caches.file_cache import FileCache
    all_sites.cachedir = '.web_cache'
except ImportError:
    pass


def prep_site_model(site):
    model = memory.connection()
    try:
        with urllib.request.urlopen(site) as resourcefp:
            sitetext = resourcefp.read()
            rdfalite.toversa(sitetext, model, site)
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        return None, e
    return model, sitetext


def get_orgname(site, reuse=None):
    '''
    Given a site URL return the org's name

    >>> from librarylink.util import all_sites, get_orgname
    >>> org = next(s for s in all_sites() if 'denverlibrary' in s.host )
    >>> get_orgname(org)
    'Denver Public Library'
    >>> get_orgname('http://link.denverlibrary.org/')
    'Denver Public Library'
    '''
    if reuse:
        model, sitetext = reuse
    else:
        model, sitetext = prep_site_model(site)
    if not model:
        return None
    for o, r, t, a in model.match(None, RDF_NS + 'type', SCHEMAORG_NS + 'Organization'):
        name = util.simple_lookup(model, o, SCHEMAORG_NS + 'name')
        if name is not None: return name
    #schema:Organization not reliable the way it's used in LLN
    #orgentity = util.simple_lookup_byvalue(model, RDF_NS + 'type', SCHEMAORG_NS + 'LibrarySystem')
    #orgentity = util.simple_lookup_byvalue(model, SCHEMAORG_NS + 'url', baseurl)
    #print(orgentity)
    #name = util.simple_lookup(model, orgentity, SCHEMAORG_NS + 'name')
    #name = util.simple_lookup(model, baseurl + '#_default', BL + 'name')
    #return name


NETWORK_HINTS = {
    #e.g. from http://augusta.library.link/
    #<link href="/static/liblink_ebsco/css/network.css" rel="stylesheet">
    b'liblink_ebsco/css/network.css': 'ebsco',
    #e.g. from http://msu.library.link/
    #<link href="/static/liblink_iii/css/network.css" rel="stylesheet"/>
    b'liblink_iii/css/network.css': 'iii',
    #e.g. from http://link.houstonlibrary.org/
    #<link href="/static/liblink_bcv/css/network.css" rel="stylesheet"/>
    b'liblink_bcv/css/network.css': 'bcv',
    #e.g. from http://link.library.gmu.edu/
    #<link href="/static/liblink_atlas/css/network.css" rel="stylesheet"/>
    b'liblink_atlas/css/network.css': 'atlas',
}


PIPELINE_VERSION_PAT = re.compile(b'<dt>Transformation Pipeline</dt>\s*<dd>([^<]*)</dd>', re.MULTILINE)
TEMPLATE_VERSION_PAT = re.compile(b'<dt>Template Version</dt>\s*<dd>([^<]*)</dd>', re.MULTILINE)
def get_orgdetails(site, reuse=None):
    '''
    Given an organization object as returned from librarylink.util.all_sites, or just a plain base URL string; return the org's name
    >>> from librarylink.util import all_sites, get_orgdetails
    >>> det = get_orgdetails('http://link.dcl.org/')
    >>> det['name']
    'Douglas County Libraries'
    >>> org = next(s for s in all_sites() if 'denverlibrary' in s.host )
    >>> det = get_orgdetails(org.base_url)
    >>> det['name']
    'Denver Public Library'
    '''
    if reuse:
        model, sitetext = reuse
    else:
        model, sitetext = prep_site_model(site)
    if not model:
        return None
    details = {'name': None, 'group': None, 'groupname': None, 'network': None, 'features': set()}
    id_ = None
    for o, r, t, a in model.match(None, RDF_NS + 'type', SCHEMAORG_NS + 'LibrarySystem'):
        id_ = o
        details['name'] = util.simple_lookup(model, o, SCHEMAORG_NS + 'name').strip()
        break

    details['id'] = id_
    #for o, r, t, a in model.match(None, SCHEMAORG_NS + 'member'):
    #    group = t.split('#')[0]
    for o, r, t, a in model.match(None, RDF_NS + 'type', SCHEMAORG_NS + 'Consortium'):
        details['group'] = util.simple_lookup(model, o, SCHEMAORG_NS + 'url')
        #group = o.split('#')[0]
        details['groupname'] = util.simple_lookup(model, o, SCHEMAORG_NS + 'name').strip()
        break

    network = 'zviz'
    for searchstr in NETWORK_HINTS:
        if searchstr in sitetext:
            details['network'] = NETWORK_HINTS[searchstr]

    m = PIPELINE_VERSION_PAT.search(sitetext)
    if m:
        details['pipeline_ver'] = m.group(1).decode('utf-8')
    else:
        details['pipeline_ver'] = None
        #print('Unable to get pipeline version from:', site)
    m = TEMPLATE_VERSION_PAT.search(sitetext)
    if m:
        details['template_ver'] = m.group(1).decode('utf-8')
    else:
        details['template_ver'] = None
        #print('Unable to get template version from:', site)

    for o, r, t, a in model.match(None, LL+'feature'):
        details['features'].add(t)

    #Legacy, for libraries where the above isn't published
    if b'<img class="img-responsive" src="/static/liblink_ea/img/nlogo.png"' in sitetext:
        details['features'].add('http://library.link/ext/feature/novelist/merge')

    details['same-as'] = []
    for o, r, t, a in model.match(None, RDF_NS + 'type', SCHEMAORG_NS + 'LibrarySystem'):
        for _, r, t, a in model.match(o, SCHEMAORG_NS + 'sameAs'):
            details['same-as'].append(t)
        break

    for o, r, t, a in model.match(None, RDF_NS + 'type', SCHEMAORG_NS + 'LibrarySystem'):
        logo = util.simple_lookup(model, o, SCHEMAORG_NS + 'logo')
        details['logo'] = logo.strip() if logo else logo
        break

    return details


def get_branches(site, reuse=None):
    '''
    Given an organization object as returned from librarylink.util.all_sites, or just a plain base URL string; return the org's name
    >>> from librarylink.util import all_sites, get_branches
    >>> org = next(s for s in all_sites() if 'denverlibrary' in s.host )
    >>> get_branches(org)
    'Denver Public Library'
    >>> get_branches('http://link.denverlibrary.org/')
    'Denver Public Library'
    '''
    if reuse:
        model, sitetext = reuse
    else:
        model, sitetext = prep_site_model(site)
    if not model:
        return None
    branches = []
    for o, r, t, a in model.match(None, RDF_NS + 'type', SCHEMAORG_NS + 'Library'):
        id_ = o
        name = util.simple_lookup(model, o, SCHEMAORG_NS + 'name').strip()
        url = util.simple_lookup(model, o, SCHEMAORG_NS + 'url')
        loc = util.simple_lookup(model, o, SCHEMAORG_NS + 'location')
        addr = util.simple_lookup(model, o, SCHEMAORG_NS + 'address')
        #Goes schema:Library - schema:location -> schema:Place - schema:geo -> Coordinates
        if loc:
            loc = util.simple_lookup(model, loc, SCHEMAORG_NS + 'geo')
        if loc:
            lat = util.simple_lookup(model, loc, SCHEMAORG_NS + 'latitude')
            long_ = util.simple_lookup(model, loc, SCHEMAORG_NS + 'longitude')

        if addr:
            #rdf:type	schema:PostalAddress
            #schema:streetAddress	"2111 Snow Road"@en
            #schema:addressLocality	"Parma"@en
            #schema:addressRegion	"OH"@en
            #schema:postalCode	"44134"@en
            #schema:addressCountry	"US"@en
            street = util.simple_lookup(model, addr, SCHEMAORG_NS + 'streetAddress')
            locality = util.simple_lookup(model, addr, SCHEMAORG_NS + 'addressLocality')
            region = util.simple_lookup(model, addr, SCHEMAORG_NS + 'addressRegion')
            postcode = util.simple_lookup(model, addr, SCHEMAORG_NS + 'postalCode')
            country = util.simple_lookup(model, addr, SCHEMAORG_NS + 'addressCountry')

        branches.append((
            id_,
            url,
            name,
            (lat, long_) if loc else None,
            (street, locality, region, postcode, country) if addr else None
        ))

    return branches


def llnurl_ident(url):
    '''
    Return the identifying pair of (site, hash) from an LLN URL

    >>> from librarylink.util import llnurl_ident
    >>> llnurl_ident('http://link.worthingtonlibraries.org/resource/9bz8W30aSZY/')
    ('link.worthingtonlibraries.org', '9bz8W30aSZY')
    >>> llnurl_ident('http://link.worthingtonlibraries.org/portal/Unshakeable--your-financial-freedom-playbook/cZlfLtSpcng/')
    ('link.worthingtonlibraries.org', 'cZlfLtSpcng')
    '''
    scheme, host, path, query, fragment = iri.split_uri_ref(url)
    try:
        if path.startswith('/resource/'):
            rhash = path.partition('/resource/')[-1].split('/')[0]
        elif '/portal/' in url:
            rhash = path.partition('/portal/')[-1].split('/')[1]
    except IndexError as e:
        #FIXME L10N
        raise ValueError('Invalid LLN URL: ' + repr(url))
    return host, rhash


def simplify_link(url):
    '''
    Return a simplified & unique form of an LLN URL

    >>> from librarylink.util import simplify_link
    >>> simplify_link('http://link.worthingtonlibraries.org/resource/9bz8W30aSZY/')
    ('link.worthingtonlibraries.org', '9bz8W30aSZY')
    >>> simplify_link('http://link.worthingtonlibraries.org/portal/Unshakeable--your-financial-freedom-playbook/cZlfLtSpcng/')
    ('link.worthingtonlibraries.org', 'cZlfLtSpcng')
    '''
    scheme, auth, path, query, fragment = iri.split_uri_ref(url)
    try:
        if path.startswith('/resource/'):
            path = '/resource/' + path.partition('/resource/')[-1].split('/')[0] + '/'
            return iri.unsplit_uri_ref((scheme, auth, path, None, None))
        if '/portal/' in url:
            path = '/portal/' + '/'.join(path.partition('/portal/')[-1].split('/')[:2]) + '/'
            return iri.unsplit_uri_ref((scheme, auth, path, None, None))
    except IndexError as e:
        #FIXME L10N
        raise ValueError('Invalid LLN URL: ' + repr(url))
    return host, rhash


class liblink_set(collections.abc.MutableSet):
    '''
    Smart collection of URLs that is smart about Library.Link URLs and how to dedup them for set operations
    '''
    def __init__(self, iterable=None):
        self.rawset = set()
        if iterable is not None:
            self |= iterable

    def add(self, item):
        simplified = simplify_link(item) or item
        self.rawset.add(simplified)

    def __len__(self):
        return len(self.rawset)

    def __contains__(self, item):
        simplified = simplify_link(item) or item
        return simplified in self.rawset

    def discard(self, item):
        simplified = simplify_link(item) or item
        self.rawset.discard(simplified)

    def __iter__(self):
        yield from self.rawset

    #?
    #def __reversed__(self):
    #def pop(self, last=True):
    #def __repr__(self):

class liblink_site(object):
    pass
