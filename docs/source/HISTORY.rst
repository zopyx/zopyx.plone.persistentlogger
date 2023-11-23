Changelog
=========

0.5.2 (2023-11-23)
------------------
- updated to DataTables 1.13.x

0.5.0 (2021-06-21)
------------------
- add file_logger module with support for "loguru" based loggers

0.4.8 (2019-03-03)
------------------
- minor UI tweaks 


0.4.7 (2018-12-20)
------------------
- Python 3 compatibility

0.4.2 (2017-01-28)
------------------
-fixes

0.4.0 (2017-01-27)
------------------
- log() supports `username` as optional argument for overriding the 
  current username
- log() now accepts an optional parameter `info_url` which can either be
  a full URL or a relative URL (relative to the Plone portal root) that will
  be displayed within the logger table under the new column `Info`

0.3.6 (2016-07-15)
------------------
- minor CSS fixes

0.3.5 (2016-04-25)
------------------
- update docs 

0.3.4 (2016-04-22)
------------------
- added preliminary toolbar icon

0.3.2 (2016-04-20)
------------------
- added browser layers
- added 'demo' profile for @@logger-demo view for creating
  some demo logger entries

0.3.1 (2016-04-20)
------------------
- fixes

0.3.0 (2016-04-20)
------------------
- full Plone 5 compatibility
- switched from DataTables.net to jsGrid


0.2.6 (2016-03-18)
------------------
- i18n_domain missing in configure.zcml

0.2.4 (2016-02-02)
------------------
- minor fixes

0.2.3 (2015-09-23)
------------------

- some unittest cleanup

0.2.2 (2015-09-23)
------------------
- log entries now store the original ``details`` value directly 
  as entry key ``details_raw`` in addition to the pretty-printed
  representation  in ``details``. Note that ``details`` must be 
  Python pickable.


0.2.1 (2015-09-17)
------------------
- changed action permission for viewing the persistent log

0.2.0 (2015-09-10)
------------------

- bugfixes, code cleanup
- added "Persistent log" object action


0.1.0 (2015-08-31)
------------------

- initial release

