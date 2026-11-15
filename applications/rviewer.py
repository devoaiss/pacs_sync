import re

import psycopg
from psycopg.rows import dict_row

import os
from tkinter import Image
import uuid
import numpy as np
import mysql.connector
from mysql.connector import Error
import hashlib

from pydicom import Dataset



from file_helper import FileHelper
from util.orthanc_util import upload_to_orthanc
import io
import requests

from PIL import Image
from pydicom import Dataset, FileDataset
from pydicom.uid import (
    ExplicitVRLittleEndian,
    SecondaryCaptureImageStorage,
)



import mysql.connector
from mysql.connector import Error


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


def generate_dicom_uid(value):
    """
    Generate a deterministic DICOM UID.

    The same value will always produce the same UID.
    """

    generated_uuid = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"oaiss-ai-dental:{value}"
    )

    return f"2.25.{generated_uuid.int}"

def create_dicom_file(file_info,patient_name):
    """
    Create a DICOM file from a BMP returned by
    file_helper.get_file_with_date().

    DICOM organization:

        Patient
        └── Study (one per patient per day)
            └── Series (one per unique BMP filename)
                └── Image

    Patient ID:
        Parent folder name.

    Image date/time:
        Extracted from BMP filename.

    StudyInstanceUID:
        Determined by patient + date.

    SeriesInstanceUID:
        Determined by unique BMP filename.

    SOPInstanceUID:
        Determined by unique BMP filename.

    No image resizing, normalization, brightness,
    contrast, or other pixel modification is performed.
    """

    image_file_path = file_info["path"]

    # ---------------------------------------------------------
    # Validate file
    # ---------------------------------------------------------

    if not os.path.isfile(image_file_path):
        raise FileNotFoundError(
            f"Image not found: {image_file_path}"
        )

    # ---------------------------------------------------------
    # Patient ID
    # ---------------------------------------------------------

    patient_id = os.path.basename(
        os.path.dirname(image_file_path)
    )

    if not patient_id:
        raise ValueError(
            f"Could not determine patient ID from: "
            f"{image_file_path}"
        )

    # ---------------------------------------------------------
    # Filename / Image ID
    # ---------------------------------------------------------

    filename = os.path.basename(image_file_path)

    image_id = os.path.splitext(filename)[0]

    # ---------------------------------------------------------
    # Extract date/time from filename
    #
    # Example:
    # 100_20523_20260519-134815_0.bmp
    #
    # Date = 20260519
    # Time = 134815
    # ---------------------------------------------------------

    match = re.search(
        r"(\d{8})-(\d{6})",
        filename
    )

    if not match:
        raise ValueError(
            f"Could not extract image date/time from filename: "
            f"{filename}"
        )

    image_date = match.group(1)
    image_time = match.group(2)

    # ---------------------------------------------------------
    # Read BMP
    # ---------------------------------------------------------

    with Image.open(image_file_path) as image:

        image.load()

        image_format = image.format
        image_mode = image.mode

        # IMPORTANT:
        # Do not resize or modify the image.
        pixel_array = np.asarray(image)

    # ---------------------------------------------------------
    # Determine image properties from BMP
    # ---------------------------------------------------------

    if pixel_array.ndim == 2:

        # Grayscale
        samples_per_pixel = 1
        photometric = "MONOCHROME2"

        rows = pixel_array.shape[0]
        columns = pixel_array.shape[1]

        pixel_array = np.ascontiguousarray(
            pixel_array
        )

    elif pixel_array.ndim == 3:

        channels = pixel_array.shape[2]

        if channels == 3:

            # RGB
            samples_per_pixel = 3
            photometric = "RGB"

            rows = pixel_array.shape[0]
            columns = pixel_array.shape[1]

            pixel_array = np.ascontiguousarray(
                pixel_array
            )

        elif channels == 4:

            raise ValueError(
                f"RGBA BMP is not supported: "
                f"{filename}"
            )

        else:

            raise ValueError(
                f"Unsupported BMP channel count: "
                f"{channels}"
            )

    else:

        raise ValueError(
            f"Unsupported BMP shape: "
            f"{pixel_array.shape}"
        )

    # ---------------------------------------------------------
    # Determine bit depth from BMP
    # ---------------------------------------------------------

    if pixel_array.dtype == np.uint8:

        bits_allocated = 8
        bits_stored = 8
        high_bit = 7

        pixel_array = np.ascontiguousarray(
            pixel_array,
            dtype=np.uint8
        )

    elif pixel_array.dtype == np.uint16:

        bits_allocated = 16
        bits_stored = 16
        high_bit = 15

        pixel_array = np.ascontiguousarray(
            pixel_array,
            dtype=np.uint16
        )

    else:

        raise ValueError(
            f"Unsupported BMP pixel type: "
            f"{pixel_array.dtype}. "
            f"Expected uint8 or uint16."
        )

    # ---------------------------------------------------------
    # DICOM UIDs
    # ---------------------------------------------------------

    # SAME patient + SAME date = SAME STUDY
    study_uid = generate_dicom_uid(
        f"study:{patient_id}:{image_date}"
    )

    # UNIQUE filename = UNIQUE SERIES
    series_uid = generate_dicom_uid(
        f"series:{image_id}"
    )

    # UNIQUE filename = UNIQUE IMAGE INSTANCE
    sop_uid = generate_dicom_uid(
        f"instance:{image_id}"
    )

    # ---------------------------------------------------------
    # File Meta Information
    # ---------------------------------------------------------

    file_meta = Dataset()

    file_meta.MediaStorageSOPClassUID = (
        SecondaryCaptureImageStorage
    )

    file_meta.MediaStorageSOPInstanceUID = (
        sop_uid
    )

    file_meta.TransferSyntaxUID = (
        ExplicitVRLittleEndian
    )

    # ---------------------------------------------------------
    # Create DICOM dataset
    # ---------------------------------------------------------

    ds = FileDataset(
        None,
        {},
        file_meta=file_meta,
        preamble=b"\0" * 128
    )

    # ---------------------------------------------------------
    # Patient
    # ---------------------------------------------------------

    ds.PatientID = patient_id
    ds.PatientName = patient_name

    # ---------------------------------------------------------
    # Study
    # ---------------------------------------------------------

    ds.StudyInstanceUID = study_uid

    ds.StudyDescription = (
        f"RViewer {image_date}"
    )

    ds.StudyDate = image_date
    ds.StudyTime = image_time

    # ---------------------------------------------------------
    # Series
    # ---------------------------------------------------------

    ds.SeriesInstanceUID = series_uid

    # We do NOT generate/increment this.
    # Orthanc can display/order the series.
    ds.SeriesNumber = "1"

    ds.SeriesDescription = (
        f"RViewer {image_id}"
    )

    # ---------------------------------------------------------
    # SOP Instance
    # ---------------------------------------------------------

    ds.SOPClassUID = (
        SecondaryCaptureImageStorage
    )

    ds.SOPInstanceUID = sop_uid

    # ---------------------------------------------------------
    # Modality
    # ---------------------------------------------------------

    ds.Modality = "IO"

    # ---------------------------------------------------------
    # Image information
    # ---------------------------------------------------------

    ds.ContentDate = image_date
    ds.ContentTime = image_time

    ds.Rows = rows
    ds.Columns = columns

    ds.SamplesPerPixel = samples_per_pixel

    ds.PhotometricInterpretation = (
        photometric
    )

    ds.BitsAllocated = bits_allocated
    ds.BitsStored = bits_stored
    ds.HighBit = high_bit

    ds.PixelRepresentation = 0

    # ---------------------------------------------------------
    # Pixel data
    # ---------------------------------------------------------

    ds.PixelData = pixel_array.tobytes()

    # ---------------------------------------------------------
    # Transfer syntax
    # ---------------------------------------------------------

    ds.is_little_endian = True
    ds.is_implicit_VR = False

    # ---------------------------------------------------------
    # Debug information
    # ---------------------------------------------------------

    return ds

def upload(config, date_from, date_to):

    connection = mysql.connector.connect(
        host=config["rviewer"]["rviewer_mysql_host"],
        user=config["rviewer"]["rviewer_mysql_user"],
        password=config["rviewer"]["rviewer_mysql_password"],
        database=config["rviewer"]["rviewer_mysql_database"],
        port=config["rviewer"]["rviewer_mysql_port"]
    )

    patients = []

    if not connection:
        print("Failed to connect to MySQL database.")
        return

    cursor = connection.cursor(dictionary=True)

    cursor.execute("SELECT PID as patient_id,Name as patient_name FROM `patient info`")

    patients = cursor.fetchall()
    # Index by patient_id
    patients_by_id = {
        patient["patient_id"]: patient
        for patient in patients
    }


    rviewer_config = config["rviewer"]
    source_path = rviewer_config["source_path"]
 

    orthanc_url = config["orthanc"]["url"]
    orthanc_username = config["orthanc"]["username"]
    orthanc_password = config["orthanc"]["password"]




    file_helper = FileHelper(
            source_path
        )
    
    
    files = file_helper.get_file_with_date(
        date_from,
        date_to,
        [".bmp"]
    )

    total_files = len(files)

    for i,file_info in enumerate(files):
        patient_id = os.path.basename(
                os.path.dirname(file_info["path"])
            )
        patient_name = patients_by_id.get(patient_id, {}).get("patient_name", "")
      


        ds = create_dicom_file(file_info,patient_name)

        filename = os.path.splitext(
            os.path.basename(file_info["path"])
        )[0]

        try:
            buffer = io.BytesIO()
        
            ds.save_as(
                buffer,
                write_like_original=False
            )
    
            dicom_bytes = buffer.getvalue()

            upload_to_orthanc(
                orthanc_url,
                orthanc_username,
                orthanc_password,
                dicom_bytes
            )

            show_progress(
                i + 1,
                total_files,
                file_path=filename
            )
        except Exception as e:
            print(
                f"Failed to upload {filename} to Orthanc: {e}"
            )

