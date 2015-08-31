zopyx.plone.persistentlogger
============================


``zopyx.plone.persistentlogger`` supports persistent logging where
the log data is stored on an arbitrary persistent Plone object.

Usage::

    from zopyx.plone.persistentlogger import IPersistentLogger

    def do_something(...):

        # ``context`` represents the current context object
        
        adapter = IPersistentLogger(context)
        adapter.log(u'this is a logging message')
        adapter.log(u'this is an error message', level='error')
        adapter.log(u'this is an error message', level='error', details='....')

``details`` can be either a string or a Python datastructure like a dict, a
list or a tuple. The logger will convert non-string data using the ``pprint``
module of Python into a nicely readable string.

The logs can be view through-the-web through the URL http://host/path/to/object/@@persistent-log .
The logs can be clear using the URL http://host/path/to/object/@@persistent-log-clear.
Both URLs require the permission of modify the related object.

Author
------
| Andreas Jung/ZOPYX
| Hundskapfklinge 33
| D-72074 Tuebingen, Germany
| info@zopyx.com
| www.zopyx.com



