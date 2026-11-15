
import os
from pathlib import Path
from PIL import Image
import uuid

import io
import requests
import numpy as np
from pydicom import Dataset
import pyodbc
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import (
    ExplicitVRLittleEndian,
    SecondaryCaptureImageStorage,
    generate_uid
)


from util.orthanc_util import upload_to_orthanc

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



def create_dicom(row, image_file_path):
    patient_id = row['patient_id']
    patient_name = row["fullname"]
    date_time = row["date_capture"]
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

   
    image_id = str(row["image_file"])

    study_uid = generate_dicom_uid(
        f"study:{patient_id}"
    )

    series_uid = generate_dicom_uid(
        f"series:{patient_id}:"
        f"{row['image_file']}"
    )




    sop_uid = generate_dicom_uid(
        f"image:{image_id}"
    )

    # ---------------------------------------------------------
    # File Meta
    # ---------------------------------------------------------

    file_meta = FileMetaDataset()

    file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()

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
    ds.PatientName = str(patient_name)

    # ---------------------------------------------------------
    # Study
    # ---------------------------------------------------------

    ds.StudyInstanceUID = study_uid
    ds.StudyDescription = "IO Photo"

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

    image_date_time = date_time

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

    

    if row.get("device"):
        ds.Manufacturer = str(
            "drsviewer"
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


def get_connection(config):
    try:

         database_path = config["drsviewer"]["database_path"]
         conn = pyodbc.connect(
                    rf"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};"
                    rf"DBQ={database_path};"
                )
    
         return conn

    except pyodbc.Error as e:
        print(f"Access connection error: {e}")

    return None

def upload(config, date_from, date_to):
    orthanc_url = config["orthanc"]["url"]
    username = config["orthanc"]["username"]
    password = config["orthanc"]["password"]
    image_path_source = config["drsviewer"]["source_path"] 
    conn = get_connection(config)
    cursor = conn.cursor()

  
    cursor.execute("""
        SELECT  
            c.ChartName AS patient_id,
            p.FirstName & ' ' & p.LastName AS fullname,
            Format(c.CaptureTime, 'YYYY-MM-DD hh:nn:ss') as date_capture,
            c.FilePath AS image_file
            
        FROM
            CaptureImg AS c
            LEFT JOIN Patient AS p
                ON c.ChartName = p.ChartName
        WHERE
            Format(c.CaptureTime, 'YYYY-MM-DD')
            >= ? and Format(c.CaptureTime, 'YYYY-MM-DD')
                        <= ? 
    """, date_from,date_to)
    columns = [column[0] for column in cursor.description]
    rows = cursor.fetchall()
    total = len(rows)

    for index, row in enumerate(rows):
        row = dict(zip(columns, row))
        patient_id = row["patient_id"]
     
  
        image_file_path = Path(image_path_source) /  patient_id  /  row["image_file"].lstrip("\\/")

        try:

            # -------------------------------------------------
            # Source image
            # -------------------------------------------------

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

            show_progress(index + 1,total ," Uploading to Orthanc")
   

        except Exception as e:

            print(
                f"ERROR processing "
                f"{patient_id}: {e}"
            )

  

    conn.close()

    
    pass