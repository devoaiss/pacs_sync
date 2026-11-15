from io import BytesIO
import os
import re

import pydicom
import requests

from file_helper import FileHelper

def extract_string(text):
    match = re.search(r'\d+', text)

    if not match:
        return text.strip(), None

    id = match.group()
    name = (text[:match.start()] + text[match.end():]).strip()
    name = re.sub(r'\s+', ' ', name)

    return name, id

def show_progress(index, total, file_path):

    percent = index / total

    bar_length = 40
    filled = int(bar_length * percent)

    bar = "█" * filled + "░" * (bar_length - filled)

    filename = os.path.basename(file_path)

    print(
        f"\r[{bar}] {percent:6.2%} "
        f"{index:,}/{total:,} | {filename:<40}",
        end="",
        flush=True
    )


def upload(config, date_from, date_to):
    """
    Upload files to Orthanc based on the provided configuration and date range.

    :param config: Configuration dictionary containing Orthanc connection details.
    :param date_from: Start date for filtering files (YYYY-MM-DD).
    :param date_to: End date for filtering files (YYYY-MM-DD).
    """
    orthanc_url = config["orthanc"]["url"]
    username = config["orthanc"]["username"]
    password = config["orthanc"]["password"]
    source_path = config["imagesensor"]["source_path"]

    # Initialize FileHelper with the source path
    file_helper = FileHelper(source_path)

    # Get files within the specified date range
    files = file_helper.get_file_with_date(date_from, date_to, [".dcm"])

    # Iterate through the files and upload them to Orthanc
    total = len(files)

    for index, file_path in enumerate(files, start=1):
        dcm_path = str(file_path["path"])
       
        # Read DICOM
        ds = pydicom.dcmread(dcm_path)
        patient_name, patient_id = extract_string(str(ds.PatientName).replace("^", " ").replace("-", " ").strip())
        ds.PatientID = patient_id
        ds.PatientName = patient_name
                # Convert modified DICOM to bytes


        if "VOILUTFunction" in ds:
            del ds.VOILUTFunction


        buffer = BytesIO()
        ds.save_as(buffer)
        # Upload to Orthanc
        buffer.seek(0)
        response = requests.post(
            f"{orthanc_url}/instances",
            auth=(username, password),
            data=buffer.getvalue(),
            headers={"Content-Type": "application/dicom"},
            timeout=60
        )
        show_progress(index, total, dcm_path)


