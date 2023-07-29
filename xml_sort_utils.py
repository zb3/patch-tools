import xml.etree.ElementTree as ET

def indent(elem, level=0):
    # Helper function to add indentation to the XML elements
    i = "\n" + level*"  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level+1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def sort_attr_elements_by_name(parent_element):
    parent_element[:] = sorted(parent_element, key=lambda elem: elem.get("name", ""))

def sort_recursive(elem, sort_func):
    if len(elem) > 0:
        sort_func(elem)
        for child in elem:
            sort_recursive(child, sort_func)

def sort_attrs_xml(xml_string):
    root = ET.fromstring(xml_string)
    sort_recursive(root, sort_attr_elements_by_name)
    indent(root)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


if __name__ == '__main__':
    xml_string = '''<?xml version="1.0" encoding="utf-8"?>

    <resources>
    <attr name="windowSoftInputMode">
        <flag name="stateUnspecified" value="0" />
        <!-- Leave the soft input window as-is, in whatever state it
            last was. -->
        <flag name="stateUnchanged" value="1" />
        <!-- Make the soft input area hidden when normally appropriate
            (when the user is navigating forward to your window). -->
        <flag name="stateHidden" value="2" />    
    </attr>
    
    <!-- comment about keycode -->
    <attr name="keycode">
        <enum name="KEYCODE_UNKNOWN" value="0" />
        <enum name="KEYCODE_SOFT_LEFT" value="1" />
        <enum name="KEYCODE_SOFT_RIGHT" value="2" />
        <enum name="KEYCODE_HOME" value="3" />
        <enum name="KEYCODE_BACK" value="4" />
        <enum name="KEYCODE_CALL" value="5" />
    </attr>
    
    <declare-styleable name="Theme">
            <eat-comment />
            <!-- test  -->
            <attr name="isLightTheme" format="boolean" />
            <!-- test2 -->
            <attr name="colorForeground" format="color" />
    </declare-styleable>
    
    </resources>
    '''

    print(sort_attrs_xml(xml_string))
