from io import BytesIO
import re

import pydicom
import requests

from applications.imagesensor import show_progress
from file_helper import FileHelper

def extract_string(text):
    match = re.search(r'\d+', text)

    if not match:
        return text.strip(), None

    id = match.group()
    name = (text[:match.start()] + text[match.end():]).strip()
    name = re.sub(r'\s+', ' ', name)

    return name, id

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
    source_path = config["dtfc"]["source_path"]

    # Initialize FileHelper with the source path
    file_helper = FileHelper(source_path)

    # Get files within the specified date range
    files = file_helper.get_file_with_date(date_from, date_to, [".dcm"])

    # Iterate through the files and upload them to Orthanc
    for index, file_path in enumerate(files):
        dcm_path = str(file_path["path"])
       

        # Read DICOM
        ds = pydicom.dcmread(dcm_path)

        # Modify PatientName and PatientID if necessary
        extracted_name, extracted_id = extract_string(str(ds.PatientName))
        ds.PatientName = extracted_name
        ds.PatientID = extracted_id
        
        # Convert modified DICOM to bytes
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

        show_progress(index + 1, len(files), file_path["path"])