from charms.reactive import when, when_not, set_flag


@when_not('osm-stack.installed')
def install_osm_stack():

    set_flag('osm-stack.installed')
