"""Provide python interface to Biolovision API.

Thin Python binding to Biolovision API, returning dict instead of JSON.
Currently, only a subset of API controlers are implemented, and only a subset
of functions and parameters for implemented controlers.
See details in each class.

Each Biolovision controler is mapped to a python class.
Class name is derived from controler name by removing '_' and using CamelCase.
Methods names are similar to the corresponding API call, prefixed by 'api_'.
For example, method 'api_list' in class 'LocalAdminUnits' will
call 'local_admin_units'.

Most notable difference is that API chunks are grouped under 'data', i.e.
calling species_list('1') will return all birds in one array under 'data' key.
This means that requests returning lots of chunks (all bird sightings !)
must be avoided, as memory could be insufficient.
max_chunks __init__ parameter controls the maximum number of chunks
allowed and raises an exception if it exceeds.

Biolovision API to Classes mapping
- BiolovisionAPI         - Top class, not for direct use

Controler | Class
----------|------
Taxo groups | TaxoGroupsAPI
Families controller | NA
Species Controller | SpeciesAPI
Territorial Units Controller | TerritorialUnitsAPI
Local admin units controller | LocalAdminUnitsAPI
Places controller | PlacesAPI
Observers | ObserversAPI
Entities | EntitiesAPI
Export organizations controller | NA
Observations Controller | ObservationsAPI
Fields controller | FieldsAPI
Media Controller | NA
Import files controller | NA
Import files/Observations controller |
Validations controller | NA
Mortality informations controller | NA
Bearded Vulture Birds controller | NA
Bearded Vulture informations controller | NA
Grids controller | NA
Grid-Commune controller | NA
Atlas documents | NA

Methods, see each class

Properties
- transfer_errors            - Return number of HTTP errors

Exceptions
- BiolovisionApiException    - General exception
- HTTPError                  - HTTP protocol error
- MaxChunksError             - Too many chunks returned from API calls
- IncorrectParameter         - Incorrect or missing parameter

"""
from copy import copy
import json
import logging
import time
import urllib
from functools import lru_cache

import requests
from requests_oauthlib import OAuth1

from . import _, __version__

logger = logging.getLogger("transfer_vn.biolovision_api")


class HashableDict(dict):
    """Provide hashable dict type, to enable @lru_cache."""

    def __hash__(self):
        return hash(frozenset(self))


class BiolovisionApiException(Exception):
    """An exception occurred while handling your request."""


class HTTPError(BiolovisionApiException):
    """An HTTP error occurred."""


class MaxChunksError(BiolovisionApiException):
    """Too many chunks returned from API calls."""


class NotImplementedException(BiolovisionApiException):
    """Feature not implemented."""


class IncorrectParameter(BiolovisionApiException):
    """Incorrect or missing parameter."""


class BiolovisionAPI:
    """Top class, not for direct use. Provides internal and template methods."""

    def __init__(
        self, config, controler, max_retry=None, max_requests=None, max_chunks=None
    ):
        self._config = config
        if max_retry is None:
            max_retry = config.tuning_max_retry
        if max_requests is None:
            max_requests = config.tuning_max_requests
        if max_chunks is None:
            max_chunks = config.tuning_max_chunks
        self._limits = {
            "max_retry": max_retry,
            "max_requests": max_requests,
            "max_chunks": max_chunks,
        }
        self._transfer_errors = 0
        self._http_status = 0
        self._ctrl = controler

        # Using OAuth1 auth helper to get access
        self._api_url = config.base_url + "api/"  # URL of API
        self._oauth = OAuth1(config.client_key, client_secret=config.client_secret)

    @property
    def version(self):
        """Return version."""
        return __version__

    @property
    def transfer_errors(self):
        """Return the number of HTTP errors during this session."""
        return self._transfer_errors

    @property
    def http_status(self):
        """Return the latest HTTP status code."""
        return self._http_status

    @property
    def controler(self):
        """Return the controler name."""
        return self._ctrl

    # ----------------
    # Internal methods
    # ----------------
    @staticmethod
    def _clean_params(params: dict):
        """Remove sensitive data from param dict."""
        c_params = copy(params)
        c_params["user_email"] = "***"
        c_params["user_pw"] = "***"
        return c_params

    def _url_get(self, params, scope, method="GET", body=None, optional_headers=None):
        """Internal function used to request from Biolovision API.

        Prepare the URL header, perform HTTP request and get json content.
        Test HTTP status and returns None if error, else return decoded json content.
        Increments _transfer_errors in case of error.

        Parameters
        ----------
        params : dict of 'parameter name': 'parameter value'.
            params is used to build URL GET string.
        scope : str
            scope is the api to be queried, for example 'taxo_groups/'.
        method : str
            HTTP method to use: GET/POST/DELETE/PUT. Default to GET
        body : str
            Optional body for POST or PUT
        optional_headers : dict
            Optional body for request

        Returns
        -------
        json : dict
            dict decoded from json if status OK, else None.

        Raises
        ------
        HTTPError
            HTTP protocol error, returned as argument.
        MaxChunksError
            Loop on chunks exceeded max_chunks limit.

        """
        # Loop on chunks
        nb_chunks = 0
        while nb_chunks < self._limits["max_chunks"]:
            # Remove DEBUG logging level to avoid too many details
            level = logging.getLogger().level
            logging.getLogger().setLevel(logging.INFO)

            # Prepare call to API
            payload = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
            logger.debug(_("Params: %s"), payload)
            headers = {"Content-Type": "application/json;charset=UTF-8"}
            if optional_headers is not None:
                headers.update(optional_headers)
            protected_url = self._api_url + scope
            if method == "GET":
                resp = requests.get(
                    url=protected_url, auth=self._oauth, params=payload, headers=headers
                )
            elif method == "POST":
                resp = requests.post(
                    url=protected_url,
                    auth=self._oauth,
                    params=payload,
                    headers=headers,
                    data=body,
                )
            else:
                raise NotImplementedException

            logger.debug(resp.headers)
            logging.getLogger().setLevel(level)
            logger.debug(
                _("%s status code = %s, for URL %s"),
                method,
                resp.status_code,
                protected_url,
            )
            self._http_status = resp.status_code
            if self._http_status != 200:
                # Request returned an error.
                # Logging and checking if not too many errors to continue
                logger.error(
                    _("%s status code = %s, for URL %s"),
                    method,
                    resp.status_code,
                    protected_url,
                )
                self._transfer_errors += 1
                time.sleep(self._config.tuning_retry_delay)
                if self._transfer_errors > self._limits["max_retry"]:
                    # Too many retries. Raising exception
                    logger.critical(
                        _("Too many error %s, raising exception"), self._transfer_errors
                    )
                    raise HTTPError(resp.status_code)
            else:
                # No error from request: processing response
                try:
                    resp_chunk = resp.json()
                except Exception:
                    # Error during JSON decoding =>
                    # Logging error and no further processing of empty chunk
                    resp_chunk = json.loads("{}")
                    logger.error(_("Incorrect response content: %s"), resp.text)
                    logger.exception(_("Exception raised during JSON decoding"))
                    self._transfer_errors += 1
                    if self._transfer_errors > self._limits["max_retry"]:
                        # Too many retries. Raising exception
                        logger.critical(
                            _("Too many error %s, raising exception"),
                            self._transfer_errors,
                        )
                        raise HTTPError("resp.json exception")

                # Initialize or append to response dict, depending on content
                if "data" in resp_chunk:
                    observations = False
                    if "sightings" in resp_chunk["data"]:
                        observations = True
                        logger.debug(
                            _("Received %d sightings in chunk %d"),
                            len(resp_chunk["data"]["sightings"]),
                            nb_chunks,
                        )
                        if nb_chunks == 0:
                            data_rec = resp_chunk
                        else:
                            if "sightings" in data_rec["data"]:
                                data_rec["data"]["sightings"] += resp_chunk["data"][
                                    "sightings"
                                ]
                            else:
                                data_rec["data"]["sightings"] = resp_chunk["data"][
                                    "sightings"
                                ]
                    if "forms" in resp_chunk["data"]:
                        observations = True
                        logger.debug(
                            _("Received %d forms in chunk %d"),
                            len(resp_chunk["data"]["forms"]),
                            nb_chunks,
                        )
                        if nb_chunks == 0:
                            data_rec = resp_chunk
                        else:
                            if "forms" in data_rec["data"]:
                                data_rec["data"]["forms"] += resp_chunk["data"]["forms"]
                            else:
                                data_rec["data"]["forms"] = resp_chunk["data"]["forms"]

                    if not observations:
                        logger.debug(
                            _("Received %d data items in chunk %d"),
                            len(resp_chunk),
                            nb_chunks,
                        )
                        if nb_chunks == 0:
                            data_rec = resp_chunk
                        else:
                            data_rec["data"] += resp_chunk["data"]
                else:
                    logger.debug(
                        _("Received %d items without data in chunk %d"),
                        len(resp_chunk),
                        nb_chunks,
                    )
                    if nb_chunks == 0:
                        data_rec = resp_chunk
                    else:
                        data_rec += resp_chunk

                # Is there more data to come?
                if (
                    ("transfer-encoding" in resp.headers)
                    and (resp.headers["transfer-encoding"] == "chunked")
                    and ("pagination_key" in resp.headers)
                ):
                    logger.debug(
                        _("Chunked transfer => requesting for more, with key: %s"),
                        resp.headers["pagination_key"],
                    )
                    # Update request parameters to get next chunk
                    params["pagination_key"] = resp.headers["pagination_key"]
                    nb_chunks += 1
                else:
                    logger.debug(_("Non-chunked transfer => finished requests"))
                    if "pagination_key" in params:
                        del params["pagination_key"]
                    break

        logger.debug(_("Received %d chunks"), nb_chunks)
        if nb_chunks >= self._limits["max_chunks"]:
            raise MaxChunksError

        return data_rec

    def _api_list(self, opt_params=None, optional_headers=None):
        """Query for a list of entities of the given controler.

        Calls /ctrl API.

        Parameters
        ----------
        opt_params : HashableDict (to enable lru_cache)
            optional URL parameters, empty by default.
            See Biolovision API documentation.
        optional_headers : dict
            Optional body for GET request

        Returns
        -------
        json : dict or None
            dict decoded from json if status OK, else None
        """
        # Mandatory parameters.
        params = {
            "user_email": self._config.user_email,
            "user_pw": self._config.user_pw,
        }
        if opt_params is not None:
            params.update(opt_params)
        logger.debug(
            _("List from:%s, with options:%s, optional_headers:%s"),
            self._ctrl,
            self._clean_params(params),
            optional_headers,
        )
        # GET from API
        entities = self._url_get(params, self._ctrl, optional_headers=optional_headers)[
            "data"
        ]
        logger.debug(_("Number of entities = %i"), len(entities))
        return {"data": entities}

    # -----------------------------------------
    #  Generic methods, used by most subclasses
    # -----------------------------------------

    def api_get(self, id_entity, **kwargs):
        """Query for a single entity of the given controler.

        Calls  /ctrl/id API.

        Parameters
        ----------
        id_entity : str
            entity to retrieve.
        **kwargs :
            optional URL parameters, empty by default.
            See Biolovision API documentation.

        Returns
        -------
        json : dict or None
            dict decoded from json if status OK, else None
        """
        # Mandatory parameters.
        params = {
            "user_email": self._config.user_email,
            "user_pw": self._config.user_pw,
        }
        for key, value in kwargs.items():
            params[key] = value
        logger.debug(
            _("In api_get for controler:%s, with parameters:%s"),
            id_entity,
            self._clean_params(params),
        )
        # GET from API
        return self._url_get(params, self._ctrl + "/" + str(id_entity))

    def api_list(self, opt_params=None, optional_headers=None):
        """Query for a list of entities of the given controler.

        Calls /ctrl API.

        Parameters
        ----------
        opt_params : dict
            optional URL parameters, empty by default.
            See Biolovision API documentation.
        optional_headers : dict
            Optional body for GET request

        Returns
        -------
        json : dict or None
            dict decoded from json if status OK, else None
        """
        h_params = None if opt_params is None else HashableDict(opt_params)
        h_headers = None if optional_headers is None else HashableDict(optional_headers)
        return self._api_list(opt_params=h_params, optional_headers=h_headers)

    # -------------------------
    # Exception testing methods
    # -------------------------
    def wrong_api(self):
        """Query for a wrong api.

        Calls /error API to raise an exception.

        """
        # Mandatory parameters.
        params = {
            "user_email": self._config.user_email,
            "user_pw": self._config.user_pw,
        }
        # GET from API
        return self._url_get(params, "error/")


class EntitiesAPI(BiolovisionAPI):
    """ Implement api calls to entities controler.

    Methods
    - api_get                - Return a single entity from the controler
    - api_list               - Return a list of entity from the controler

    """

    def __init__(self, config, max_retry=None, max_requests=None, max_chunks=None):
        super().__init__(config, "entities", max_retry, max_requests, max_chunks)


class FieldsAPI(BiolovisionAPI):
    """ Implement api calls to fields controler.

    Methods
    - api_get                - Return a single entity from the controler
    - api_list               - Return a list of entity from the controler

    """

    def __init__(self, config, max_retry=None, max_requests=None, max_chunks=None):
        super().__init__(config, "fields", max_retry, max_requests, max_chunks)


class LocalAdminUnitsAPI(BiolovisionAPI):
    """ Implement api calls to local_admin_units controler.

    Methods
    - api_get                - Return a single entity from the controler
    - api_list               - Return a list of entity from the controler

    """

    def __init__(self, config, max_retry=None, max_requests=None, max_chunks=None):
        super().__init__(
            config, "local_admin_units", max_retry, max_requests, max_chunks
        )


class ObservationsAPI(BiolovisionAPI):
    """ Implement api calls to observations controler.

    Methods
    - api_get      - Return a single observations from the controler
    - api_list     - Return a list of observations from the controler
    - api_diff     - Return all changes in observations since a given date
    - api_search   - Search for observations based on parameter value
    """

    def __init__(self, config, max_retry=None, max_requests=None, max_chunks=None):
        super().__init__(config, "observations", max_retry, max_requests, max_chunks)

    def api_list(self, id_taxo_group, **kwargs):
        """Query for list of observations by taxo_group from the controler.

        Calls  /observations API.

        Parameters
        ----------
        id_taxo_group : integer
            taxo_group to query for observations
        **kwargs :
            optional URL parameters, empty by default.
            See Biolovision API documentation.

        Returns
        -------
        json : dict or None
            dict decoded from json if status OK, else None
        """
        opt_params = dict()
        opt_params["id_taxo_group"] = str(id_taxo_group)
        for key, value in kwargs.items():
            opt_params[key] = value
        logger.debug(_("In api_list, with parameters %s"), opt_params)
        return super().api_list(opt_params)

    def api_diff(self, id_taxo_group, delta_time, modification_type="all"):
        """Query for a list of updates or deletions since a given date.

        Calls /observations/diff to get list of created/updated or deleted
        observations since a given date (max 10 weeks backward).

        Parameters
        ----------
        id_taxo_group : str
            taxo group from which to query diff.
        delta_time : str
            Start of time interval to query.
        modification_type : str
            Type of diff queried : can be only_modified, only_deleted or
            all (default).

        Returns
        -------
        json : dict or None
            dict decoded from json if status OK, else None
        """
        # Mandatory parameters.
        params = {
            "user_email": self._config.user_email,
            "user_pw": self._config.user_pw,
        }
        # Specific parameters.
        params["id_taxo_group"] = str(id_taxo_group)
        params["modification_type"] = modification_type
        params["date"] = delta_time
        # GET from API
        return super()._url_get(params, "observations/diff/")

    def api_search(self, q_params, **kwargs):
        """Search for observations, based on parameter conditions.

        Calls /observations/search to get observations
        same parameters than in online version can be used

        Parameters
        ----------
        q_params : dict
            Query parameters, same as online version.

        Returns
        -------
        json : dict or None
            dict decoded from json if status OK, else None
        **kwargs :
            optional URL parameters, empty by default.
            See Biolovision API documentation.
        """
        # Mandatory parameters.
        params = {
            "user_email": self._config.user_email,
            "user_pw": self._config.user_pw,
        }
        for key, value in kwargs.items():
            params[key] = value
        # Specific parameters.
        if q_params is not None:
            body = json.dumps(q_params)
        else:
            raise IncorrectParameter
        logger.debug(
            _("Search from %s, with option %s and body %s"),
            self._ctrl,
            self._clean_params(params),
            body,
        )
        # GET from API
        return super()._url_get(params, "observations/search/", "POST", body)


class ObserversAPI(BiolovisionAPI):
    """ Implement api calls to observers controler.

    Methods
    - api_get                - Return a single entity from the controler
    - api_list               - Return a list of entity from the controler

    """

    def __init__(self, config, max_retry=None, max_requests=None, max_chunks=None):
        super().__init__(config, "observers", max_retry, max_requests, max_chunks)


class PlacesAPI(BiolovisionAPI):
    """ Implement api calls to places controler.

    Methods
    - api_get                - Return a single place from the controler
    - api_list               - Return a list of places from the controler

    """

    def __init__(self, config, max_retry=None, max_requests=None, max_chunks=None):
        super().__init__(config, "places", max_retry, max_requests, max_chunks)


class SpeciesAPI(BiolovisionAPI):
    """ Implement api calls to species controler.

    Methods
    - api_get                - Return a single specie from the controler
    - api_list               - Return a list of species from the controler

    """

    def __init__(self, config, max_retry=None, max_requests=None, max_chunks=None):
        super().__init__(config, "species", max_retry, max_requests, max_chunks)


class TaxoGroupsAPI(BiolovisionAPI):
    """ Implement api calls to taxo_groups controler.

    Methods
    - api_get                - Return a single taxo group from the controler
    - api_list               - Return a list of taxo groups from the controler

    """

    def __init__(self, config, max_retry=None, max_requests=None, max_chunks=None):
        super().__init__(config, "taxo_groups", max_retry, max_requests, max_chunks)

    @lru_cache(maxsize=32)
    def api_list(self, opt_params=None):
        """Return list of taxo groups, from cache or site."""
        return super().api_list()


class TerritorialUnitsAPI(BiolovisionAPI):
    """ Implement api calls to territorial_units controler.

    Methods
    - api_get                - Return a single territorial unit from the controler
    - api_list               - Return a list of territorial units from the controler

    """

    def __init__(self, config, max_retry=None, max_requests=None, max_chunks=None):
        super().__init__(
            config, "territorial_units", max_retry, max_requests, max_chunks
        )

    @lru_cache(maxsize=32)
    def api_list(self, opt_params=None):
        """Return list of taxo groups, from cache or site."""
        return super().api_list()