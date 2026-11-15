from importlib.resources import files
from io import BytesIO
import os
from pathlib import Path
import re
import hashlib

import numpy as np
import requests

from PIL import Image
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import (
    ExplicitVRLittleEndian,
    SecondaryCaptureImageStorage,
)

from file_helper import FileHelper


IMAGE_EXTENSIONS = [
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp"
]


def read_patient_dat(patient_dat_path):

    patient = {}

    with open(
        patient_dat_path,
        "r",
        encoding="utf-16-le",
        errors="replace"
    ) as f:

        for line in f:

            line = line.strip()

            if line.startswith("NumINSEE="):

                patient["patient_id"] = (
                    line.split("=", 1)[1].strip()
                )

            elif line.startswith("Nom="):

                patient["last_name"] = (
                    line.split("=", 1)[1].strip()
                )
            elif line.startswith("Type="):
            
                            patient["type"] = (
                                line.split("=", 1)[1].strip()
                            )

            elif line.startswith("Prenom="):

                patient["first_name"] = (
                    line.split("=", 1)[1].strip()
                )

    return patient


# =========================================================
# SHA-256
# =========================================================

def get_file_sha256(path):

    sha256 = hashlib.sha256()

    with open(path, "rb") as f:

        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b""
        ):
            sha256.update(chunk)

    return sha256.hexdigest()


# =========================================================
# DICOM UID
# =========================================================

def deterministic_dicom_uid(value, root="2.25"):

    digest = hashlib.sha1(
        value.encode("utf-8")
    ).hexdigest()

    number = int(digest, 16)

    return f"{root}.{number}"


# =========================================================
# READ IMAGE.DAT
# =========================================================

def get_image_details(image_path, image_dat_path):

    image_name = Path(image_path).name

    with open(
        image_dat_path,
        "r",
        encoding="utf-16-le",
        errors="replace"
    ) as f:

        content = f.read()

    pattern = (
        r"\["
        + re.escape(image_name)
        + r"\]"
        r"\s*"
        r"(.*?)"
        r"(?=\n\[|\Z)"
    )

    match = re.search(
        pattern,
        content,
        re.IGNORECASE | re.DOTALL
    )

    if not match:

        raise ValueError(
            f"{image_name} was not found in "
            f"{image_dat_path}"
        )

    section = match.group(1)

    details = {}

    for line in section.splitlines():

        line = line.strip()

        if not line or "=" not in line:
            continue

        key, value = line.split("=", 1)

        details[key.strip()] = value.strip()

    return {
        "date": details.get("Date"),
        "time": details.get("Heure")
    }


# =========================================================
# CREATE DICOM
# =========================================================
def image_is_color(image):
    rgb = np.asarray(image.convert("RGB"))

    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]

    return np.any((r != g) | (r != b) | (g != b))


def create_dicom(
    patient_id,
    patient_name,
    image_path,
    image_dat_path
):

    image_path = Path(image_path)
    image_dat_path = Path(image_dat_path)

    # -----------------------------------------------------
    # Get date/time from SOPRO Image.dat
    # -----------------------------------------------------

    image_details = get_image_details(
        image_path,
        image_dat_path
    )

    study_date = image_details["date"]
    study_time = image_details["time"]

    if not study_date:

        raise ValueError(
            f"No Date found for {image_path.name}"
        )

    if not study_time:
        study_time = "000000"

    patient_id = str(patient_id)
    patient_name = str(patient_name)
    study_date = str(study_date)
    study_time = str(study_time)

    filename = image_path.name

    # -----------------------------------------------------
    # Hash original image
    # -----------------------------------------------------

    image_hash = get_file_sha256(
        image_path
    )

    # -----------------------------------------------------
    # Study UID
    # -----------------------------------------------------

    study_uid = deterministic_dicom_uid(
        f"STUDY|{patient_id}|{study_date}"
    )

    # -----------------------------------------------------
    # Series UID
    # -----------------------------------------------------

    series_uid = deterministic_dicom_uid(
        f"SERIES|"
        f"{patient_id}|"
        f"{study_date}|"
        f"{study_time}|"
        f"{filename}|"
        f"{image_hash}"
    )

    # -----------------------------------------------------
    # SOP Instance UID
    # -----------------------------------------------------

    sop_instance_uid = deterministic_dicom_uid(
        f"IMAGE|"
        f"{patient_id}|"
        f"{study_date}|"
        f"{study_time}|"
        f"{filename}|"
        f"{image_hash}"
    )

    # -----------------------------------------------------
    # File Meta
    # -----------------------------------------------------

    file_meta = FileMetaDataset()

    file_meta.MediaStorageSOPClassUID = (
        SecondaryCaptureImageStorage
    )

    file_meta.MediaStorageSOPInstanceUID = (
        sop_instance_uid
    )

    file_meta.TransferSyntaxUID = (
        ExplicitVRLittleEndian
    )

    file_meta.ImplementationClassUID = (
        deterministic_dicom_uid(
            "OAISS-BRACES-CENTRE-DICOM"
        )
    )

    # -----------------------------------------------------
    # Dataset
    # -----------------------------------------------------

    ds = FileDataset(
        "",
        {},
        file_meta=file_meta,
        preamble=b"\0" * 128
    )

    ds.is_little_endian = True
    ds.is_implicit_VR = False

    # -----------------------------------------------------
    # Patient
    # -----------------------------------------------------

    ds.PatientID = patient_id
    ds.PatientName = patient_name

    # -----------------------------------------------------
    # Study
    # -----------------------------------------------------

    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = sop_instance_uid

    ds.StudyID = (
        f"{patient_id}{study_date}"
    )

    ds.SeriesNumber = "1"

    ds.SOPClassUID = (
        SecondaryCaptureImageStorage
    )

    # -----------------------------------------------------
    # Date / Time from SOPRO
    # -----------------------------------------------------

    ds.StudyDate = study_date
    ds.StudyTime = study_time

    ds.AcquisitionDate = study_date
    ds.AcquisitionTime = study_time

    ds.ContentDate = study_date
    ds.ContentTime = study_time

    # -----------------------------------------------------
    # Modality
    # -----------------------------------------------------

    ds.Modality = "IO"

    # -----------------------------------------------------
    # Image
    # -----------------------------------------------------

    image = Image.open(image_path)

    # Handle different image formats/modes
    if image.mode == "RGBA":
        # Remove alpha channel and keep RGB
        image = image.convert("RGB")

    elif image.mode == "CMYK":
        # Convert CMYK to RGB
        image = image.convert("RGB")

    elif image.mode == "P":
        # Palette image -> RGB first
        image = image.convert("RGB")

    elif image.mode == "LA":
        # Grayscale + alpha -> grayscale
        image = image.convert("L")

    elif image.mode not in ("RGB", "L"):
        # Other modes -> RGB
        image = image.convert("RGB")


    # Now determine whether the actual image contains color
    if image.mode == "RGB" and image_is_color(image):
        # Color image
        ds.SamplesPerPixel = 3
        ds.PhotometricInterpretation = "RGB"
        ds.PlanarConfiguration = 0

    else:
        # Grayscale image
        image = image.convert("L")

        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"


    ds.Rows = image.height
    ds.Columns = image.width

    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0

    ds.PixelData = image.tobytes()

    return ds


# =========================================================
# MAIN SOPRO UPLOAD
# =========================================================
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



    # -----------------------------------------------------
    # Configuration
    # -----------------------------------------------------

    orthanc_url = config["orthanc"]["url"].rstrip("/")

    username = config["orthanc"]["username"]
    password = config["orthanc"]["password"]

    source_path = config["sopro"]["source_path"]

    auth = (
        username,
        password
    )

    # -----------------------------------------------------
    # Find files by Windows creation date
    # -----------------------------------------------------

    file_helper = FileHelper(
        source_path
    )

    files = file_helper.get_file_with_date(
        date_from,
        date_to,
        IMAGE_EXTENSIONS
    )

    total = len(files)

    # -----------------------------------------------------
    # Process files
    # -----------------------------------------------------

    for index, file_path in enumerate(files, start=1):
        
  

        image_path = Path(
            file_path["path"]
        )

        folder_path = Path(
            file_path["folder"]
        )


   
        # -------------------------------------------------
        # Patient.dat
        # -------------------------------------------------

        patient_dat = (
            folder_path / "Patient.dat"
        )

        if not patient_dat.exists():

            print(
                f"Patient.dat not found: "
                f"{patient_dat}"
            )

            continue

        patient = read_patient_dat(
            patient_dat
        )

        patient_id = patient.get(
            "patient_id"
        )

        last_name = patient.get(
            "last_name",
            ""
        )

        first_name = patient.get(
            "first_name",
            ""
        )

        if not patient_id:

            print(
                "Patient ID not found"
            )

            continue

        patient_name = (
            f"{last_name} {first_name}"
        )

   
        # -------------------------------------------------
        # Image.dat
        # -------------------------------------------------

        image_dat = (
            folder_path / "Image.dat"
        )

        if not image_dat.exists():

            print(
                f"Image.dat not found: "
                f"{image_dat}"
            )

            continue

        # -------------------------------------------------
        # Create DICOM
        # -------------------------------------------------

        try:

            ds = create_dicom(
                patient_id=patient_id,
                patient_name=patient_name,
                image_path=image_path,
                image_dat_path=image_dat
            )

        except Exception as e:

            print(
                f"Failed to create DICOM: {e}"
            )

            continue


        # -------------------------------------------------
        # Convert DICOM to bytes
        # -------------------------------------------------

        buffer = BytesIO()

        ds.save_as(
            buffer,
            write_like_original=False
        )

        dicom_bytes = buffer.getvalue()

        # -------------------------------------------------
        # Upload to Orthanc
        # -------------------------------------------------

        try:

            response = requests.post(
                f"{orthanc_url}/instances",
                data=dicom_bytes,
                headers={
                    "Content-Type": "application/dicom"
                },
                auth=auth,
                timeout=30
            )

            response.raise_for_status()

            result = response.json()

      

        except requests.exceptions.RequestException as e:

            print(
                f"✗ Orthanc upload failed: {e}"
            )
        show_progress(index, total, file_path["path"])