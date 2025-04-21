import json


def _find_unclosed(json_str):
    """
    Identifies the unclosed braces and brackets in the JSON string.

    Args:
        json_str (str): The JSON string to analyze.

    Returns:
        list: A list of unclosed elements in the order they were opened.
    """
    unclosed = []
    inside_string = False
    escape_next = False

    for char in json_str:
        if inside_string:
            if escape_next:
                escape_next = False
            elif char == "\\":
                escape_next = True
            elif char == '"':
                inside_string = False
        else:
            if char == '"':
                inside_string = True
            elif char in "{[":
                unclosed.append(char)
            elif char in "}]":
                if unclosed and ((char == "}" and unclosed[-1] == "{") or (char == "]" and unclosed[-1] == "[")):
                    unclosed.pop()

    return unclosed


# The following code is used to fix a broken JSON string.
# From HippoRAG2 (GitHub: OSU-NLP-Group/HippoRAG)
def fix_broken_generated_json(json_str: str) -> str:
    """
    Fixes a malformed JSON string by:
    - Removing the last comma and any trailing content.
    - Iterating over the JSON string once to determine and fix unclosed braces or brackets.
    - Ensuring braces and brackets inside string literals are not considered.

    If the original json_str string can be successfully loaded by json.loads(), will directly return it without any modification.

    Args:
        json_str (str): The malformed JSON string to be fixed.

    Returns:
        str: The corrected JSON string.
    """

    try:
        # Try to load the JSON to see if it is valid
        json.loads(json_str)
        return json_str  # Return as-is if valid
    except json.JSONDecodeError:
        pass

    # Step 1: Remove trailing content after the last comma.
    last_comma_index = json_str.rfind(",")
    if last_comma_index != -1:
        json_str = json_str[:last_comma_index]

    # Step 2: Identify unclosed braces and brackets.
    unclosed_elements = _find_unclosed(json_str)

    # Step 3: Append the necessary closing elements in reverse order of opening.
    closing_map = {"{": "}", "[": "]"}
    for open_char in reversed(unclosed_elements):
        json_str += closing_map[open_char]

    return json_str
