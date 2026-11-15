import os
from tkinter import Image
import uuid
import numpy as np
import mysql.connector

from mysql.connector import Error
import hashlib

from pydicom import Dataset
from applications.imagesensor import show_progress
from util.orthanc_util import upload_to_orthanc

import os
import io
import requests

from PIL import Image
from pydicom import Dataset, FileDataset
from pydicom.uid import (
    ExplicitVRLittleEndian,
    SecondaryCaptureImageStorage,
)



def get_connection(config):
    try:
        conn = mysql.connector.connect(
            host=config["aidental"]["aidental_mysql_host"],
            port=config["aidental"]["aidental_mysql_port"],
            user=config["aidental"]["aidental_mysql_user"],
            password=config["aidental"]["aidental_mysql_password"],
            database=config["aidental"]["aidental_mysql_database"]
        )

        if conn.is_connected():
            return conn

    except Error as e:
        print(f"MySQL connection error: {e}")

    return None

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

def create_dicom(row, image_file_path):
    """
    Create a DICOM dataset from an AI-Dental source image.

    IMPORTANT:
    - Does NOT resize the image.
    - Does NOT change pixel values.
    - Does NOT change brightness.
    - Does NOT change contrast.
    - Does NOT apply normalization.
    - Does NOT apply windowing to PixelData.
    - Preserves the source bit depth where possible.

    The image pixels stored in DICOM are the original source pixels.
    """

    if not os.path.isfile(image_file_path):
        raise FileNotFoundError(
            f"Image not found: {image_file_path}"
        )

    # ---------------------------------------------------------
    # Open source image
    # ---------------------------------------------------------

    image = Image.open(image_file_path)



    # ---------------------------------------------------------
    # Convert image to NumPy WITHOUT changing pixels
    # ---------------------------------------------------------

    pixel_array = np.asarray(image)

 

    # ---------------------------------------------------------
    # Determine image type
    # ---------------------------------------------------------

    if pixel_array.ndim == 2:

        # -----------------------------------------------------
        # Grayscale
        # -----------------------------------------------------

        samples_per_pixel = 1
        photometric = "MONOCHROME2"

        rows = pixel_array.shape[0]
        columns = pixel_array.shape[1]

        # Make sure memory layout is contiguous.
        # This does NOT change pixel values.
        pixel_array = np.ascontiguousarray(pixel_array)

    elif pixel_array.ndim == 3:

        if pixel_array.shape[2] == 3:

            # -----------------------------------------------------
            # RGB
            # -----------------------------------------------------

            samples_per_pixel = 3
            photometric = "RGB"

            rows = pixel_array.shape[0]
            columns = pixel_array.shape[1]

            pixel_array = np.ascontiguousarray(
                pixel_array,
                dtype=np.uint8
            )

        elif pixel_array.shape[2] == 4:

            # -----------------------------------------------------
            # RGBA
            # -----------------------------------------------------

            alpha = pixel_array[:, :, 3]

          

            # -----------------------------------------------------
            # If alpha is completely opaque, dropping it does NOT
            # change the visible RGB image.
            # -----------------------------------------------------

            if np.all(alpha == 255):

        

                pixel_array = pixel_array[:, :, :3]

                samples_per_pixel = 3
                photometric = "RGB"

                rows = pixel_array.shape[0]
                columns = pixel_array.shape[1]

                pixel_array = np.ascontiguousarray(
                    pixel_array,
                    dtype=np.uint8
                )

            else:

                raise ValueError(
                    "RGBA image contains transparent pixels. "
                    "Alpha compositing would modify the visible "
                    "image, so the source image was not modified."
                )

        else:

            raise ValueError(
                f"Unsupported color image shape: "
                f"{pixel_array.shape}"
            )

            # -----------------------------------------------------
            # Color
            # -----------------------------------------------------

            if pixel_array.shape[2] not in (3, 4):
                raise ValueError(
                    f"Unsupported color image shape: "
                    f"{pixel_array.shape}"
                )

            # Pillow may return RGBA.
            # DICOM RGB does not contain alpha.
            #
            # If alpha exists, dropping alpha technically changes
            # the representation, so explicitly reject it rather
            # than silently manipulate the source.
            if pixel_array.shape[2] == 4:
                raise ValueError(
                    "RGBA images are not supported because "
                    "removing the alpha channel would modify "
                    "the source image."
                )

            samples_per_pixel = 3
            photometric = "RGB"

            rows = pixel_array.shape[0]
            columns = pixel_array.shape[1]

            pixel_array = np.ascontiguousarray(pixel_array)

    else:

        raise ValueError(
            f"Unsupported image shape: "
            f"{pixel_array.shape}"
        )

    # ---------------------------------------------------------
    # Determine pixel data type
    # ---------------------------------------------------------

    if not np.issubdtype(pixel_array.dtype, np.integer):
        raise ValueError(
            f"Unsupported pixel dtype: "
            f"{pixel_array.dtype}"
        )

    # ---------------------------------------------------------
    # Preserve source bit depth
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
            f"Unsupported source pixel dtype: "
            f"{pixel_array.dtype}. "
            f"Expected uint8 or uint16."
        )

    # ---------------------------------------------------------
    # Generate deterministic UIDs
    # ---------------------------------------------------------

    patient_id = str(row["patientId"])
    image_id = str(row["imageId"])

    study_uid = generate_dicom_uid(
        f"study:{patient_id}"
    )

    series_uid = generate_dicom_uid(
        f"series:{patient_id}:"
        f"{row['device']}:"
        f"{row['imageType']}"
    )

    sop_uid = generate_dicom_uid(
        f"image:{image_id}"
    )

    # ---------------------------------------------------------
    # File Meta
    # ---------------------------------------------------------

    file_meta = Dataset()

    file_meta.MediaStorageSOPClassUID = (
        SecondaryCaptureImageStorage
    )

    file_meta.MediaStorageSOPInstanceUID = sop_uid

    file_meta.TransferSyntaxUID = (
        ExplicitVRLittleEndian
    )

    # ---------------------------------------------------------
    # Create DICOM
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
    ds.PatientName = str(row["patient_name"])

    # ---------------------------------------------------------
    # Study
    # ---------------------------------------------------------

    ds.StudyInstanceUID = study_uid
    ds.StudyDescription = "Dental X-Ray"

    # ---------------------------------------------------------
    # Series
    # ---------------------------------------------------------

    ds.SeriesInstanceUID = series_uid
    ds.SeriesNumber = "1"

    # ---------------------------------------------------------
    # SOP
    # ---------------------------------------------------------

    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = sop_uid

    # ---------------------------------------------------------
    # Modality
    # ---------------------------------------------------------

    ds.Modality = "IO"

    # ---------------------------------------------------------
    # Date / Time
    # ---------------------------------------------------------

    image_date_time = row.get("imageTime")

    if image_date_time:

        image_date_time = str(image_date_time)

        if len(image_date_time) >= 10:
            ds.StudyDate = (
                image_date_time[:10]
                .replace("-", "")
            )

        if len(image_date_time) >= 19:
            ds.StudyTime = (
                image_date_time[11:19]
                .replace(":", "")
            )

    # ---------------------------------------------------------
    # X-Ray parameters
    # ---------------------------------------------------------

    if row.get("voltage"):
        ds.KVP = str(row["voltage"])

    if row.get("current"):
        ds.XRayTubeCurrent = str(row["current"])

    if row.get("exposureTime"):
        ds.ExposureTime = str(row["exposureTime"])

    # ---------------------------------------------------------
    # Dental information
    # ---------------------------------------------------------

    if row.get("toothLocation"):
        ds.BodyPartExamined = str(row["toothLocation"]).upper()[:16]

    if row.get("annotation"):
        ds.ImageComments = str(
            row["annotation"]
        )[:1024]

    # ---------------------------------------------------------
    # Device
    # ---------------------------------------------------------

    if row.get("device"):
        ds.Manufacturer = str(
            row["device"]
        )[:64]

    # ---------------------------------------------------------
    # Image properties
    # ---------------------------------------------------------

    ds.Rows = rows
    ds.Columns = columns

    ds.SamplesPerPixel = samples_per_pixel
    ds.PhotometricInterpretation = photometric

    ds.BitsAllocated = bits_allocated
    ds.BitsStored = bits_stored
    ds.HighBit = high_bit

    # Dental X-ray grayscale images are normally unsigned.
    # For RGB this is also the appropriate representation.
    ds.PixelRepresentation = 0

    # ---------------------------------------------------------
    # IMPORTANT:
    #
    # Do NOT calculate or assign WindowCenter / WindowWidth.
    #
    # We want OHIF to receive the source pixel data without
    # forcing a new brightness/contrast presentation.
    # ---------------------------------------------------------

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
    # Diagnostic output
    # ---------------------------------------------------------



    return ds

def upload(config, date_from, date_to):




    connection = get_connection(config)



    if not connection:
        print("Failed to connect to MySQL database.")
        return
    
    orthanc_url = config["orthanc"]["url"]
    username = config["orthanc"]["username"]
    password = config["orthanc"]["password"]
    image_folder = config["aidental"]["source_path"]
   
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
    """
    SELECT
        i.originalImage,
        i.zoomImage,
        i.patientId as patient_raw_id,
        p.idCard AS patientId,
        i.imageId,
        CONCAT(p.name, ' ', p.surname) AS patient_name,

        i.toothType,
        i.toothLocation,

        i.voltage,
        i.current,
        i.exposureTime,
        i.dose,

        i.imageTime,
        i.device,
        i.imageType,

        i.annotation,
        i.measurementFactor,
        i.nLuminance

    FROM image AS i
    INNER JOIN patient AS p
        ON i.patientId = p.id

    WHERE i.createtime BETWEEN
        CONCAT(%s, ' 00:00:00')
        AND CONCAT(%s, ' 23:59:59')
        limit 1
        """,
        (date_from.strip(), date_to.strip())
    )


    rows = cursor.fetchall()

    total = len(rows)

    for  row in rows:
      
        patient_name = row['patient_name']
        patient_id = row['patientId']
        image_date_time = row['imageTime']
        

        # Generate DICOM dataset

        try:

            # -------------------------------------------------
            # Source image
            # -------------------------------------------------

            image_file_path = os.path.join(
                image_folder,
                str(row["patient_raw_id"]),
                str(row["zoomImage"])
            )

            if not os.path.isfile(image_file_path):

                # print(
                #     f"Image not found: "
                #     f"{image_file_path}"
                # )

                continue

            ds = create_dicom(row, image_file_path)

            buffer = io.BytesIO()

            ds.save_as(
                buffer,
                write_like_original=False
            )

            dicom_bytes = buffer.getvalue()


            upload_result = upload_to_orthanc(
                orthanc_url,
                username,
                password,
                dicom_bytes
            )

            show_progress(total, rows.index(row) + 1," Uploading to Orthanc")

        except Exception as e:

            print(
                f"ERROR processing "
                f"{row.get('imageId')}: {e}"
            )

    cursor.close()
    connection.close()