# -*- coding: utf-8 -*-

################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################


import os
import unittest
import plone.api
from plone.app.testing import PloneSandboxLayer
from plone.app.testing import applyProfile
from plone.app.testing import PLONE_FIXTURE
from plone.app.testing import IntegrationTesting
from plone.app.testing import setRoles
from plone.app.testing import login
from plone.testing import z2
from plone.registry.interfaces import IRegistry

from zope.component import getUtility
from zope.configuration import xmlconfig
from AccessControl.SecurityManagement import newSecurityManager


import zopyx.plone.persistentlogger 

class PolicyFixture(PloneSandboxLayer):

    defaultBases = (PLONE_FIXTURE,)

    def setUpZope(self, app, configurationContext):
        #        xmlconfig.file('meta.zcml', z3c.jbot, context=configurationContext)

        for mod in [zopyx.plone.persistentlogger,
                    ]:
            xmlconfig.file('configure.zcml', mod, context=configurationContext)


    def setUpPloneSite(self, portal):
        # Install into Plone site using portal_setup
        applyProfile(portal, 'zopyx.plone.persistentlogger:default')
        portal.acl_users.userFolderAddUser('god', 'dummy', ['Manager'], [])
        setRoles(portal, 'god', ['Manager'])
        login(portal, 'god')

    def tearDownZope(self, app):
        z2.uninstallProduct(app, 'zopyx.plone.persistentlogger')


POLICY_FIXTURE = PolicyFixture()
POLICY_INTEGRATION_TESTING = IntegrationTesting(
    bases=(POLICY_FIXTURE,), name='PolicyFixture:Integration')


class TestBase(unittest.TestCase):

    layer = POLICY_INTEGRATION_TESTING

    @property
    def portal(self):
        return self.layer['portal']

    def login(self, uid='god'):
        """ Login as manager """
        user = self.portal.acl_users.getUser(uid)
        newSecurityManager(None, user.__of__(self.portal.acl_users))
