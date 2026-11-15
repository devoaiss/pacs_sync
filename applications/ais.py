import datetime
import os
from pathlib import Path
import re
import psycopg
from psycopg.rows import dict_row

from tkinter import Image
import uuid
import numpy as np
import mysql.connector
from mysql.connector import Error
import hashlib

from pydicom import Dataset
from applications.imagesensor import show_progress

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

def create_dicom_file(file_info):
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

    patient_id = file_info["patient_id"]

    if not patient_id:
        raise ValueError(
            f"Could not determine patient ID from: "
            f"{image_file_path}"
        )

    # ---------------------------------------------------------
    # Filename / Image ID
    # ---------------------------------------------------------

    filename = os.path.basename(image_file_path)

    image_id = file_info["image_id"]
    image_date = file_info["image_date"]
    image_time = file_info["image_time"]

    # ---------------------------------------------------------
    # Extract date/time from filename
    #
    # Example:
    # 100_20523_20260519-134815_0.bmp
    #
    # Date = 20260519
    # Time = 134815
    # ---------------------------------------------------------





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

    series_uid = generate_dicom_uid(
        f"series:{patient_id}:{image_date}:{image_time}:{image_id}"
    )

    sop_uid = generate_dicom_uid(
        f"instance:{patient_id}:{image_date}:{image_time}:{image_id}"
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
    ds.PatientName = ""

    # ---------------------------------------------------------
    # Study
    # ---------------------------------------------------------

    ds.StudyInstanceUID = study_uid

    ds.StudyDescription = (
        f"AIS {image_date}"
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





    #check connection to the database
    orthanc_url = config["orthanc"]["url"]
    orthanc_username = config["orthanc"]["username"]
    orthanc_password = config["orthanc"]["password"]

    conn = psycopg.connect(
        host=config["ais"]["ais_postgres_host"],
        port=config["ais"]["ais_postgres_port"],
        dbname=config["ais"]["ais_postgres_database"],
        user=config["ais"]["ais_postgres_user"],
        password=config["ais"]["ais_postgres_password"],
        row_factory=dict_row
    )
    source_folder = config["ais"]["source_path"]

    
    cur = conn.cursor().execute(
                """
                select
                
                    to_char(pa."acqDate", 'YYYYMMDD') AS image_date,
                    to_char(pa."acqDate", 'HH24MISS') AS image_time,
                    pa.id as image_id,
                    p."patient_code" as patient_code,
                    to_char(pa."acqDate", 'YYYY-MM-DD_HH24-MI-SS') || '.dgi.tif' AS image_filename,
                    p.dcm_id as patient_id
                from 
                    "patientsAcq" pa join patients p 
                        on pa."idPatient" = p.id 
                    join "PERSONALDATA" pd 
                        on p.id_personal_data = pd."ID"                        
                where
                    pa."acqDate"::date >= %s AND pa."acqDate"::date <= %s
                """
                , (date_from, date_to)
            )
    
    rows = cur.fetchall()
 
        
    file_help = FileHelper(source_folder)
    files = file_help.get_file_with_date(date_from, date_to, [".tif"])


    for i, file_info in enumerate(files):
        image_file_path = file_info["path"]
        patient_code = Path(image_file_path).parent.name
        result = next(
            (row for row in rows if row["patient_code"] == patient_code),
            None
        )
        created_datetime = datetime.datetime.fromtimestamp(file_info["created"])
        image_date = created_datetime.strftime("%Y%m%d")
        image_time = created_datetime.strftime("%H%M%S")
        ds = create_dicom_file({
            "path": image_file_path,
            "patient_id": result["patient_id"],
            "image_date": image_date,
            "image_time": image_time,
            "image_id" : result["image_id"],

     })
        buffer = io.BytesIO()
        
        ds.save_as(
            buffer,
            write_like_original=False
        )

        dicom_bytes = buffer.getvalue()

      

        orthanc_result = upload_to_orthanc(
            orthanc_url,
            orthanc_username,
            orthanc_password,
            dicom_bytes
        )

        show_progress(
            i + 1,
            len(files),
            image_file_path
        )
        








     


            
    