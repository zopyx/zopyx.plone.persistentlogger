#!/bin/bash

export PATH=\
/opt/buildout.python/bin:\
$PATH:

if [[ "$1" = "plone-4.3" ]]
then
    config=buildout-plone-4.3.cfg
fi

if [[ "$1" = "plone-5.0" ]]
then
    config=buildout-plone-5.0.cfg
fi

if [[ "$1" = "plone-5.1" ]]
then
    config=buildout-plone-5.1.cfg
fi


virtualenv --clear .
bin/pip install -r requirements.txt
bin/buildout bootstrap
bin/buildout -c $config
bin/test -s zopyx.plone.persistentlogger
