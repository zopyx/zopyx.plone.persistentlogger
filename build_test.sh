#!/bin/bash

export PATH=\
/opt/buildout.python/bin:\
$PATH:

if [[ "$1" = "plone-5.2" ]]
then
    config=buildout.cfg
fi


virtualenv --clear .
bin/pip install -r requirements.txt
bin/buildout bootstrap
bin/buildout -c $config
bin/test -s zopyx.plone.persistentlogger
