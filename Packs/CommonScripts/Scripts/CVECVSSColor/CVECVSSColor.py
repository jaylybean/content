
from CommonServerPython import *
from typing import Union
import json


def get_color(cvss: Union[int, float]) -> str:
    """
    Gets a CVSS score as an integer\float and sends back the correct hex code for a color as a string.

    Args:
        cvss (int\float): A CVE CVSS score.

    Returns:
        str: The color of the score in hex format
    """

    colors = {
        'Green1': '#50C878',
        'Green2': '#6CB65B',
        'Green3': '#89C35B',
        'Green4': '#A3C157',
        'Amber1': '#FFB347',
        'Amber2': '#FFA07A',
        'Amber3': '#FF7F50',
        'Red1': '#FF6347',
        'Red2': '#FF4500',
        'Red3': '#FF4040'
    }

    if not 0 < cvss <= 10:
        color = "#000000"
    elif cvss <= 4:
        color = colors[f"Green{int(cvss)}"]
    elif cvss <= 7:
        color = colors[f"Amber{int(cvss) - 4}"]
    else:
        color = colors[f"Red{int(cvss) - 7}"]

    return color


def main():
    indicator = demisto.callingContext['args']['indicator']

    cvss = indicator.get('CustomFields').get('cvss', '')
    cvss = json.loads(cvss)
    cvss = float(cvss.get('Score', 0))

    color = get_color(cvss)
    return_results(CommandResults(readable_output=f"# <-:->{{{{color:{color}}}}}(**{cvss}**)"))


if __name__ in ('__builtin__', 'builtins', '__main__'):
    main()
