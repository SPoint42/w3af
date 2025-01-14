"""
knowledge_base.py

Copyright 2006 Andres Riancho

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
import collections
import functools
import threading
import cPickle
import copy

from w3af.core.data.fuzzer.utils import rand_alpha
from w3af.core.data.db.dbms import get_default_persistent_db_instance
from w3af.core.controllers.exceptions import DBException
from w3af.core.data.db.disk_set import DiskSet
from w3af.core.data.misc.cpickle_dumps import cpickle_dumps
from w3af.core.data.parsers.url import URL
from w3af.core.data.request.fuzzable_request import FuzzableRequest
from w3af.core.data.kb.vuln import Vuln
from w3af.core.data.kb.info import Info
from w3af.core.data.kb.shell import Shell
from w3af.core.data.kb.info_set import InfoSet
from w3af.core.data.constants.severity import (INFORMATION, LOW, MEDIUM, HIGH)
from weakref import WeakValueDictionary


class BasicKnowledgeBase(object):
    """
    This is a base class from which all implementations of KnowledgeBase will
    inherit. It has the basic utility methods that will be used.

    :author: Andres Riancho (andres.riancho@gmail.com)
    """
    UPDATE = 'update'
    APPEND = 'append'
    ADD_URL = 'add_url'

    def __init__(self):
        self._kb_lock = threading.RLock()

        self.FILTERS = {'URL': self.filter_url,
                        'VAR': self.filter_var}

    def append_uniq(self, location_a, location_b, info_inst, filter_by='VAR'):
        """
        Append to a location in the KB if and only if there it no other
        vulnerability in the same location for the same URL and parameter.

        Does this in a thread-safe manner.

        :param filter_by: One of 'VAR' of 'URL'. Only append to the kb in
                          (location_a, location_b) if there is NO OTHER info
                          in that location with the same:
                              - 'VAR': URL,Variable,DataContainer.keys()
                              - 'URL': URL

        :return: True if the vuln was added. False if there was already a
                 vulnerability in the KB location with the same URL and
                 parameter.
        """
        if not isinstance(info_inst, Info):
            raise ValueError('append_uniq requires an info object as parameter.')

        filter_function = self.FILTERS.get(filter_by, None)

        if filter_function is None:
            raise ValueError('append_uniq only knows about URL or VAR filters.')

        with self._kb_lock:

            if filter_function(location_a, location_b, info_inst):
                self.append(location_a, location_b, info_inst)
                return True

            return False

    def filter_url(self, location_a, location_b, info_inst):
        """
        :return: True if there is no other info in (location_a, location_b)
                 with the same URL as the info_inst.
        """
        for saved_vuln in self.get(location_a, location_b):
            if saved_vuln.get_url() == info_inst.get_url():
                return False

        return True

    def filter_var(self, location_a, location_b, info_inst):
        """
        :return: True if there is no other info in (location_a, location_b)
                 with the same URL,Variable,DataContainer.keys() as the
                 info_inst.
        """
        for saved_vuln in self.get(location_a, location_b):

            if saved_vuln.get_token_name() == info_inst.get_token_name() and\
            saved_vuln.get_url() == info_inst.get_url():

                if saved_vuln.get_dc() is None and\
                info_inst.get_dc() is None:
                    return False

                if saved_vuln.get_dc() is not None and\
                info_inst.get_dc() is not None:

                    if saved_vuln.get_dc().keys() == info_inst.get_dc().keys():
                        return False

        return True

    def append_uniq_group(self, location_a, location_b, info_inst,
                          group_klass=InfoSet):
        """
        This function will append a Info instance to an existing InfoSet which
        is stored in (location_a, location_b) and matches the filter_func.

        If filter_func doesn't match any existing InfoSet instances, then a new
        one is created using `group_klass` and `info_inst` is appended to it.

        :see: https://github.com/andresriancho/w3af/issues/3955

        :param location_a: The "a" address
        :param location_b: The "b" address
        :param info_inst: The Info instance we want to store
        :param group_klass: If required, will be used to create a new InfoSet
        :return: (The updated/created InfoSet, as stored in the kb,
                  True if a new InfoSet was created)
        """
        if not isinstance(info_inst, Info):
            raise TypeError('append_uniq_group requires an Info instance'
                            ' as parameter.')

        if not issubclass(group_klass, InfoSet):
            raise TypeError('append_uniq_group requires an InfoSet subclass'
                            ' as parameter.')

        with self._kb_lock:
            for info_set in self.get(location_a, location_b):
                if not isinstance(info_set, InfoSet):
                    continue

                if info_set.match(info_inst):
                    old_info_set = copy.deepcopy(info_set)
                    info_set.add(info_inst)
                    self.update(old_info_set, info_set)
                    return info_set, False
            else:
                # No pre-existing InfoSet instance matched, let's create one
                # for the info_inst
                info_set = group_klass([info_inst])
                self.append(location_a, location_b, info_set)
                return info_set, True

    def get_all_vulns(self):
        """
        :return: A list of all info instances with severity in (LOW, MEDIUM,
                 HIGH)
        """
        raise NotImplementedError

    def get_all_infos(self):
        """
        :return: A list of all info instances with severity eq INFORMATION
        """
        raise NotImplementedError

    def get_all_findings(self):
        """
        :return: A list of all findings, including Info, Vuln and InfoSet.
        """
        return self.get_all_entries_of_class((Info, InfoSet, Vuln))

    def get_all_shells(self, w3af_core=None):
        """
        :param w3af_core: The w3af_core used in the current scan
        @see: Shell.__reduce__ to understand why we need the w3af_core
        :return: A list of all vulns reported by all plugins.
        """
        all_shells = []

        for shell in self.get_all_entries_of_class(Shell):
            if w3af_core is not None:
                shell.set_url_opener(w3af_core.uri_opener)
                shell.set_worker_pool(w3af_core.worker_pool)

            all_shells.append(shell)

        return all_shells

    def _get_real_name(self, data):
        if isinstance(data, basestring):
            return data
        else:
            return data.get_name()

    def append(self, location_a, location_b, value):
        """
        This method appends the location_b value to a dict.
        """
        raise NotImplementedError

    def get(self, plugin_name, location_b, check_types=True):
        """
        :param plugin_name: The plugin that saved the data to the
                                kb.info Typically the name of the plugin,
                                but could also be the plugin instance.

        :param location_b: The name of the variables under which the vuln
                                 objects were saved. Typically the same name of
                                 the plugin, or something like "vulns", "errors",
                                 etc. In most cases this is NOT None. When set
                                 to None, a dict with all the vuln objects found
                                 by the plugin_name is returned.

        :return: Returns the data that was saved by another plugin.
        """
        raise NotImplementedError

    def get_all_entries_of_class(self, klass):
        """
        :return: A list of all objects of class == klass that are saved in the
                 kb.
        """
        raise NotImplementedError

    def update(self, old_vuln, update_vuln):
        """
        :return: The updated vulnerability/info instance stored in the kb.
        """
        raise NotImplementedError

    def clear(self, location_a, location_b):
        """
        Clear any values stored in (location_a, location_b)
        """
        raise NotImplementedError

    def raw_write(self, location_a, location_b, value):
        """
        This method saves the value to (location_a,location_b)
        """
        raise NotImplementedError

    def raw_read(self, location_a, location_b):
        """
        This method reads the value from (location_a,location_b)
        """
        raise NotImplementedError

    def dump(self):
        raise NotImplementedError

    def cleanup(self):
        """
        Cleanup all internal data.
        """
        raise NotImplementedError


def requires_setup(_method):

    @functools.wraps(_method)
    def decorated(self, *args, **kwargs):
        if not self.initialized:
            self.setup()

        return _method(self, *args, **kwargs)

    return decorated


class DBKnowledgeBase(BasicKnowledgeBase):
    """
    This class saves the data that is sent to it by plugins. It is the only way
    in which plugins can exchange information.

    Data is stored in a DB.

    :author: Andres Riancho (andres.riancho@gmail.com)
    """
    COLUMNS = [('location_a', 'TEXT'),
               ('location_b', 'TEXT'),
               ('uniq_id', 'TEXT'),
               ('pickle', 'BLOB')]

    def __init__(self):
        super(DBKnowledgeBase, self).__init__()
        self.initialized = False

        # TODO: Why doesn't this work with a WeakValueDictionary?
        self.observers = {} #WeakValueDictionary()
        self._observer_id = 0

    def setup(self):
        """
        Setup all the required backend stores. This was mostly created to avoid
        starting any threads during __init__() which is called during python's
        import phase and dead-locks in some cases.

        :return: None
        """
        with self._kb_lock:

            # Only initialize once
            if self.initialized:
                return

            self.initialized = True

            self.urls = DiskSet(table_prefix='kb_urls')
            self.fuzzable_requests = DiskSet(table_prefix='kb_fuzzable_requests')

            self.db = get_default_persistent_db_instance()

            self.table_name = 'knowledge_base_' + rand_alpha(30)
            self.db.create_table(self.table_name, self.COLUMNS)
            self.db.create_index(self.table_name, ['location_a', 'location_b'])
            self.db.create_index(self.table_name, ['uniq_id'])
            self.db.commit()

    @requires_setup
    def clear(self, location_a, location_b):
        location_a = self._get_real_name(location_a)

        query = "DELETE FROM %s WHERE location_a = ? and location_b = ?"
        params = (location_a, location_b)
        self.db.execute(query % self.table_name, params)

    @requires_setup
    def raw_write(self, location_a, location_b, value):
        """
        This method saves value to (location_a,location_b) but previously
        clears any pre-existing values.
        """
        if isinstance(value, Info):
            raise TypeError('Use append or append_uniq to store vulnerabilities')

        location_a = self._get_real_name(location_a)

        self.clear(location_a, location_b)
        self.append(location_a, location_b, value, ignore_type=True)

    @requires_setup
    def raw_read(self, location_a, location_b):
        """
        This method reads the value from (location_a, location_b)
        """
        location_a = self._get_real_name(location_a)
        result = self.get(location_a, location_b, check_types=False)

        if len(result) > 1:
            msg = 'Incorrect use of raw_write/raw_read, found %s results.'
            raise RuntimeError(msg % len(result))
        elif len(result) == 0:
            return []
        else:
            return result[0]

    @requires_setup
    def get_one(self, location_a, location_b):
        """
        This method reads the value from (location_a, location_b), checking it's
        type and making sure only one is stored at that address.

        Similar to raw_read, but checking types.

        :see: https://github.com/andresriancho/w3af/issues/3955
        """
        location_a = self._get_real_name(location_a)
        result = self.get(location_a, location_b, check_types=True)

        if len(result) > 1:
            msg = 'Incorrect use of get_one(), found %s results.'
            raise RuntimeError(msg % result)
        elif len(result) == 0:
            return []
        else:
            return result[0]

    def _get_uniq_id(self, obj):
        if isinstance(obj, (Info, InfoSet)):
            return obj.get_uniq_id()
        else:
            if isinstance(obj, collections.Iterable):
                concat_all = ''.join([str(i) for i in obj])
                return str(hash(concat_all))
            else:
                return str(hash(obj))

    @requires_setup
    def append(self, location_a, location_b, value, ignore_type=False):
        """
        This method appends the location_b value to a dict.
        """
        if not ignore_type and not isinstance(value, (Info, Shell, InfoSet)):
            msg = 'You MUST use raw_write/raw_read to store non-info objects'\
                  ' to the KnowledgeBase.'
            raise TypeError(msg)

        location_a = self._get_real_name(location_a)
        uniq_id = self._get_uniq_id(value)

        pickled_obj = cpickle_dumps(value)
        t = (location_a, location_b, uniq_id, pickled_obj)

        query = "INSERT INTO %s VALUES (?, ?, ?, ?)" % self.table_name
        self.db.execute(query, t)
        self._notify_observers(self.APPEND, location_a, location_b, value,
                               ignore_type=ignore_type)

    @requires_setup
    def get(self, location_a, location_b, check_types=True):
        """
        :param location_a: The plugin that saved the data to the
                           kb.info Typically the name of the plugin,
                           but could also be the plugin instance.

        :param location_b: The name of the variables under which the vuln
                           objects were saved. Typically the same name of
                           the plugin, or something like "vulns", "errors",
                           etc. In most cases this is NOT None. When set
                           to None, a dict with all the vuln objects found
                           by the plugin_name is returned.

        :return: Returns the data that was saved by another plugin.
        """
        location_a = self._get_real_name(location_a)

        if location_b is None:
            query = 'SELECT pickle FROM %s WHERE location_a = ?'
            params = (location_a,)
        else:
            query = 'SELECT pickle FROM %s WHERE location_a = ?'\
                                           ' and location_b = ?'
            params = (location_a, location_b)

        result_lst = []

        results = self.db.select(query % self.table_name, params)
        for r in results:
            obj = cPickle.loads(r[0])

            if check_types and not isinstance(obj, (Info, InfoSet, Shell)):
                raise TypeError('Use raw_write and raw_read to query the'
                                ' knowledge base for non-Info objects')

            result_lst.append(obj)

        return result_lst

    @requires_setup
    def get_by_uniq_id(self, uniq_id):
        query = 'SELECT pickle FROM %s WHERE uniq_id = ?'
        params = (uniq_id,)

        result = self.db.select_one(query % self.table_name, params)

        if result is not None:
            result = cPickle.loads(result[0])

        return result

    @requires_setup
    def update(self, old_info, update_info):
        """
        :param old_info: The info/vuln instance to be updated in the kb.
        :param update_info: The info/vuln instance with new information
        :return: Nothing
        """
        old_not_info = not isinstance(old_info, (Info, InfoSet, Shell))
        update_not_info = not isinstance(update_info, (Info, InfoSet, Shell))

        if old_not_info or update_not_info:
            msg = 'You MUST use raw_write/raw_read to store non-info objects'\
                  ' to the KnowledgeBase.'
            raise TypeError(msg)

        old_uniq_id = old_info.get_uniq_id()
        new_uniq_id = update_info.get_uniq_id()
        pickle = cpickle_dumps(update_info)

        # Update the pickle and unique_id after finding by original uniq_id
        query = "UPDATE %s SET pickle = ?, uniq_id = ? WHERE uniq_id = ?"

        params = (pickle, new_uniq_id, old_uniq_id)
        result = self.db.execute(query % self.table_name, params).result()

        if result.rowcount:
            self._notify_observers(self.UPDATE, old_info, update_info)
        else:
            ex = 'Failed to update() %s instance because' \
                 ' the original unique_id (%s) does not exist in the DB,' \
                 ' or the new unique_id (%s) is invalid.'
            raise DBException(ex % (old_info.__class__.__name__,
                                    old_uniq_id,
                                    new_uniq_id))

    def add_observer(self, observer):
        """
        Add the observer instance to the list.
        """
        observer_id = self.get_observer_id()
        self.observers[observer_id] = observer

    def get_observer_id(self):
        self._observer_id += 1
        return self._observer_id

    def _notify_observers(self, method, *args, **kwargs):
        """
        Call the observer if the location_a/location_b matches with the
        configured observers.

        :return: None
        """
        # Note that I copy the items list in order to iterate though it without
        # any issues like the size changing
        for _, observer in self.observers.items()[:]:
            functor = getattr(observer, method)
            functor(*args, **kwargs)

    @requires_setup
    def get_all_entries_of_class(self, klass):
        """
        :return: A list of all objects of class == klass that are saved in the
                 kb.
        """
        query = 'SELECT pickle FROM %s'
        results = self.db.select(query % self.table_name)

        result_lst = []

        for r in results:
            obj = cPickle.loads(r[0])
            if isinstance(obj, klass):
                result_lst.append(obj)

        return result_lst

    @requires_setup
    def get_all_vulns(self):
        """
        :return: A list of all info instances with severity in (LOW, MEDIUM,
                 HIGH)
        """
        query = 'SELECT pickle FROM %s'
        results = self.db.select(query % self.table_name)

        result_lst = []

        for r in results:
            obj = cPickle.loads(r[0])
            if hasattr(obj, 'get_severity'):
                severity = obj.get_severity()
                if severity in (LOW, MEDIUM, HIGH):
                    result_lst.append(obj)

        return result_lst

    @requires_setup
    def get_all_infos(self):
        """
        :return: A list of all info instances with severity eq INFORMATION
        """
        query = 'SELECT pickle FROM %s'
        results = self.db.select(query % self.table_name)

        result_lst = []

        for r in results:
            obj = cPickle.loads(r[0])
            if hasattr(obj, 'get_severity'):
                severity = obj.get_severity()
                if severity in (INFORMATION,):
                    result_lst.append(obj)

        return result_lst

    @requires_setup
    def dump(self):
        result_dict = {}

        query = 'SELECT location_a, location_b, pickle FROM %s'
        results = self.db.select(query % self.table_name)

        for location_a, location_b, pickle in results:
            obj = cPickle.loads(pickle)

            if location_a not in result_dict:
                result_dict[location_a] = {location_b: [obj,]}
            elif location_b not in result_dict[location_a]:
                result_dict[location_a][location_b] = [obj,]
            else:
                result_dict[location_a][location_b].append(obj)

        return result_dict

    @requires_setup
    def cleanup(self):
        """
        Cleanup internal data.
        """
        self.db.execute("DELETE FROM %s WHERE 1=1" % self.table_name)

        # Remove the old, create new.
        self.urls.cleanup()
        self.urls = DiskSet(table_prefix='kb_urls')

        self.fuzzable_requests.cleanup()
        self.fuzzable_requests = DiskSet(table_prefix='kb_fuzzable_requests')

        self.observers.clear()

    @requires_setup
    def remove(self):
        self.db.drop_table(self.table_name)
        self.urls.cleanup()
        self.fuzzable_requests.cleanup()
        self.observers.clear()

    @requires_setup
    def get_all_known_urls(self):
        """
        :return: A DiskSet with all the known URLs as URL objects.
        """
        return self.urls

    @requires_setup
    def add_url(self, url):
        """
        :return: True if the URL was previously unknown
        """
        if not isinstance(url, URL):
            msg = 'add_url requires a URL as parameter got %s instead.'
            raise TypeError(msg % type(url))

        self._notify_observers(self.ADD_URL, url)
        return self.urls.add(url)

    @requires_setup
    def get_all_known_fuzzable_requests(self):
        """
        :return: A DiskSet with all the known URLs as URL objects.
        """
        return self.fuzzable_requests

    @requires_setup
    def add_fuzzable_request(self, fuzzable_request):
        """
        :return: True if the FuzzableRequest was previously unknown
        """
        if not isinstance(fuzzable_request, FuzzableRequest):
            msg = 'add_fuzzable_request requires a FuzzableRequest as '\
                  'parameter, got "%s" instead.'
            raise TypeError(msg % type(fuzzable_request))

        self.add_url(fuzzable_request.get_url())
        return self.fuzzable_requests.add(fuzzable_request)




KnowledgeBase = DBKnowledgeBase

kb = KnowledgeBase()
