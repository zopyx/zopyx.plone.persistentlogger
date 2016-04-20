################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################


from zope.interface import Interface


class BrowserLayer(Interface):
    pass


class DemoBrowserLayer(BrowserLayer):
    pass
