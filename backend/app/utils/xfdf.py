"""XFDF 직렬화/역직렬화 유틸리티.

Apryse WebViewer에서 사용하는 XFDF(XML Forms Data Format) 형식의
주석 데이터를 파싱하고 생성하는 유틸리티 함수를 제공한다.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

# XFDF XML namespace
XFDF_NAMESPACE = "http://ns.adobe.com/xfdf/"
XFDF_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    f'<xfdf xmlns="{XFDF_NAMESPACE}" xml:space="preserve">'
    "<annots/>"
    "</xfdf>"
)


def validate_xfdf(xfdf_data: str) -> bool:
    """XFDF 데이터가 유효한 XML인지 검증한다.

    Returns:
        True if valid XFDF XML, False otherwise.
    """
    try:
        root = ET.fromstring(xfdf_data)
        # Check root tag is 'xfdf' (with or without namespace)
        tag = root.tag
        if tag == "xfdf" or tag.endswith("}xfdf"):
            return True
        return False
    except ET.ParseError:
        return False


def empty_xfdf() -> str:
    """빈 XFDF 문서를 반환한다."""
    return XFDF_TEMPLATE


def remove_annotation_from_xfdf(xfdf_data: str, annotation_id: str) -> str | None:
    """XFDF 데이터에서 특정 annotation을 제거한다.

    Args:
        xfdf_data: XFDF XML 문자열
        annotation_id: 제거할 annotation의 name 속성 값

    Returns:
        수정된 XFDF 문자열, 또는 annotation을 찾지 못하면 None
    """
    try:
        # Register namespace to preserve it in output
        ET.register_namespace("", XFDF_NAMESPACE)
        root = ET.fromstring(xfdf_data)
    except ET.ParseError:
        return None

    # Find annots element (with or without namespace)
    ns = {"xfdf": XFDF_NAMESPACE}
    annots = root.find("xfdf:annots", ns)
    if annots is None:
        annots = root.find("annots")
    if annots is None:
        return None

    found = False
    for child in list(annots):
        if child.get("name") == annotation_id:
            annots.remove(child)
            found = True

    if not found:
        return None

    return ET.tostring(root, encoding="unicode", xml_declaration=True)
