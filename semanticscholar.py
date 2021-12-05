from typing import Literal
import requests
import urllib.parse
import logging
from datetime import timedelta
from ratelimit import limits, sleep_and_retry
from tenacity import (retry,
                      wait_fixed,
                      retry_if_exception_type,
                      stop_after_attempt)


class SemanticScholar:

    DEFAULT_API_URL = 'https://api.semanticscholar.org/v1'
    DEFAULT_SEARCH_API_URL = 'https://api.semanticscholar.org/graph/v1/paper'
    DEFAULT_PARTNER_API_URL = 'https://partner.semanticscholar.org/v1'

    auth_header = {}

    def __init__(
                self,
                timeout: int=240,
                api_key: str=None,
                api_url: str=None
            ) -> None:
        '''
        :param float timeout: an exception is raised
            if the server has not issued a response for timeout seconds.
        :param str api_key: (optional) private API key.
        :param str api_url: (optional) custom API url.
        '''

        if api_url:
            self.api_url = api_url
        else:
            self.api_url = self.DEFAULT_API_URL

        if api_key:
            self.auth_header = {'x-api-key': api_key}
            if not api_url:
                self.api_url = self.DEFAULT_PARTNER_API_URL

        self.api_search_url = self.DEFAULT_SEARCH_API_URL
        self.timeout = timeout

    def findItem(self, doi, title) -> dict:
        smdata = None
        if doi and doi.startswith("10."):
            smdata = self.paper(id=doi)
        else:
            smdata = self.search(title)
        return smdata

    def paper(self, id: str, include_unknown_refs: bool=False) -> dict:
        '''Paper lookup
        :param str id: S2PaperId, DOI or ArXivId.
        :param float timeout: an exception is raised
            if the server has not issued a response for timeout seconds.
        :param bool include_unknown_refs:
            (optional) include non referenced paper.
        :returns: paper data or empty :class:`dict` if not found.
        :rtype: :class:`dict`
        '''

        data = self.__get_data('paper', id, include_unknown_refs)

        return data

    def search(self, id: str, include_unknown_refs: bool=False) -> dict:
        '''
        http://api.semanticscholar.org/graph/v1/paper/search?query=literature+graph
        '''
        id = urllib.parse.quote_plus(id)
        data = self.__get_data('search', id, include_unknown_refs)

        try:
            return self.paper(id=data['data'][0]['paperId'])
        except:
            return None

    def searchTitle(self, id: str, include_unknown_refs: bool=False) -> dict:
        '''
        http://api.semanticscholar.org/graph/v1/paper/search?query=literature+graph
        '''
        id = urllib.parse.quote_plus(id)
        data = self.__get_data('search', id, include_unknown_refs)

        return data


    def author(self, id: str) -> dict:
        '''Author lookup
        :param str id: S2AuthorId.
        :returns: author data or empty :class:`dict` if not found.
        :rtype: :class:`dict`
        '''

        data = self.__get_data('author', id, False)

        return data

    @sleep_and_retry
    @limits(calls=1, period=timedelta(seconds=10).total_seconds())
    @retry(
        wait=wait_fixed(240),
        retry=(retry_if_exception_type(ConnectionRefusedError) | retry_if_exception_type(PermissionError) | retry_if_exception_type(TimeoutError)),
        stop=stop_after_attempt(10)
    )
    def __get_data(
                self,
                method: Literal['paper', 'author', 'search'],
                id: str,
                include_unknown_refs: bool
            ) -> dict:
        '''Get data from Semantic Scholar API
        :param str method: 'paper' or 'author'.
        :param str id: id of the corresponding method.
        :returns: data or empty :class:`dict` if not found.
        :rtype: :class:`dict`
        '''

        data = {}
        method_types = ['paper', 'author', 'search']
        if method not in method_types:
            raise ValueError(
                'Invalid method type. Expected one of: {}'.format(method_types)
            )

        if method != 'search':
            url = '{}/{}/{}'.format(self.api_url, method, id)
            if include_unknown_refs:
                url += '?include_unknown_references=true'
        else:
            url = '{}/{}?query={}&limit=10&fields=title'.format(self.api_search_url, method, id)
            
        
        #logging.info("Semantic Scholar %s" %(url))
        r = requests.get(url, timeout=self.timeout, headers=self.auth_header)

        #logging.info("Semantic Scholar %d %s" %(r.status_code, url))
        if r.status_code == 200:
            data = r.json()
            if len(data) == 1 and 'error' in data:
                data = {}
        elif r.status_code == 403:
            raise PermissionError('HTTP status 403 Forbidden.')
        elif r.status_code == 429:
            raise ConnectionRefusedError('HTTP status 429 Too Many Requests.')
        elif r.status_code == 504:
            raise TimeoutError('HTTP status 504 Connection Timeout.')

        return data
