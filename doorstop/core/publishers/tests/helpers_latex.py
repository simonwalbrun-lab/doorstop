# SPDX-License-Identifier: LGPL-3.0-only

"""Unit test helper functions to reduce code duplication."""

# pylint: disable=unused-argument,protected-access

YAML_LATEX_DOC = """
settings:
  digits: 3
  prefix: REQ
  sep: '-'
attributes:
  doc:
    name: 'Tutorial'
    title: 'Development test document'
    ref: 'TUT-DS-22'
    by: 'Jng'
    major: '1'
    minor: 'A'
    copyright: 'Whatever Inc.'
  publish:
    - CUSTOM-ATTRIB
    - invented-by
"""

YAML_LATEX_EMPTY_DOC = """
settings:
  digits: 3
  prefix: TST
  sep: '-'
attributes:
  doc:
    name: ''
    title: ''
    ref: ''
    by: ''
    major: ''
    minor: ''
    copyright: ''
  publish:
    - CUSTOM-ATTRIB
"""

YAML_LATEX_NO_DOC = """
settings:
  digits: 3
  prefix: TST
  sep: '-'
attributes:
  defaults:
  publish:
    - CUSTOM-ATTRIB
"""

# Legacy layout (doc under attributes.defaults) kept to guard backward compatibility.
YAML_LATEX_NO_REF = """
settings:
  digits: 3
  prefix: TST
  sep: '-'
attributes:
  defaults:
    doc:
      name: 'Tutorial'
      title: 'Development test document'
      by: 'Jng'
      major: '1'
      minor: 'A'
      copyright: 'Whatever Inc.'
  publish:
    - CUSTOM-ATTRIB
    - invented-by
"""

YAML_LATEX_ONLY_REF = """
settings:
  digits: 3
  prefix: TST
  sep: '-'
attributes:
  doc:
    ref: 'TUT-DS-22'
  publish:
    - CUSTOM-ATTRIB
    - invented-by
"""
