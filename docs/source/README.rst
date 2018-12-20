zopyx.plone.persistentlogger
============================

``zopyx.plone.persistentlogger`` supports persistent logging where the log data
is stored on an arbitrary persistent Plone object (as annotation).  Typical
usecases are application specific logging e.g. for logging a history per
content object directly in Plone rather then having a huge common log on the
filesystem. The log entries are stored using object annotations.

Usage::

    from zopyx.plone.persistentlogger.logger import IPersistentLogger

    def do_something(...):

        # ``context`` represents the current context object
        
        adapter = IPersistentLogger(context)
        adapter.log(u'this is a logging message')
        adapter.log(u'this is an error message', level='error')
        adapter.log(u'this is an error message', level='error', details='....')

``details`` can be either a string or a Python datastructure like a dict, a
list or a tuple. The logger will convert non-string data using the ``pprint``
module of Python into a nicely readable string.
``level`` can be an arbitrary string for indicating the severity of the logging
message.  The module does not perform any checking on the given message
``level``. Sorting on ``level`` is accomplished only on the lexicographical
ordering of the ``level`` values.

The logs can be view through-the-web through the URL http://host/path/to/object/@@persistent-log .
The logs can be clear using the URL http://host/path/to/object/@@persistent-log-clear.
Both URLs require the permission of modify the related object.

All logs can be searched, sorted and filtered individually based on the Datatables.net
implementation.

Compatibility
-------------

- Plone 4.3
- Plone 5.0
- Plone 5.1
- Plone 5.2 (Python 3.6+, Python 2.7)

Installation
------------

Installation on Plone 4.3.X requires the following version pinning::
 
  plone.app.jquery = 1.8.3

Repository & issue tracker
--------------------------

- https://github.com/zopyx/zopyx.plone.persistentlogger

.. image:: https://travis-ci.org/zopyx/zopyx.plone.persistentlogger.svg?branch=master



Author
------
| Andreas Jung/ZOPYX
| Hundskapfklinge 33
| D-72074 Tuebingen, Germany
| info@zopyx.com
| www.zopyx.com

