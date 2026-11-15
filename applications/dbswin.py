from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from io import BytesIO
import os
import os
from pathlib import Path

import numpy as np
import pydicom
import requests
from PIL import Image, ImageOps
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

from file_helper import FileHelper

# Digital Intra-oral X-Ray Image Storage - For Presentation
IO_FOR_PRESENTATION = "1.2.840.10008.5.1.4.1.1.1.3"
UID_ROOT = "1.2.826.0.1.3680043.8.498"
DELPHI_EPOCH = datetime(1899, 12, 30)

# .dcm files are written next to this source file
OUTPUT_DIR = Path(__file__).resolve().parent

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
def delphi_to_datetime(value):
    if value in (None, "", 0, "0"):
        return None
    try:
        return DELPHI_EPOCH + timedelta(days=float(value))
    except (TypeError, ValueError):
        return None


def parse_ini(path: Path) -> dict:
    """Parse DBSWIN INI-like sidecar into {section: {key: value}}."""
    sections = {}
    current = None
    text = path.read_text(encoding="latin-1", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, {})
            continue
        if current is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        sections[current][key.strip()] = value.strip()
    return sections


def ini_get(sections: dict, section: str, key: str, default: str = "") -> str:
    return str(sections.get(section, {}).get(key, default)).strip()


def uid_from(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    return f"{UID_ROOT}.{int(digest[:16], 16)}"


def parse_rotates_and_invert(sections: dict) -> tuple[bool, int]:
    invert = False
    degrees = 0
    ops = sections.get("ImageOperations", {})
    for value in ops.values():
        if value.startswith("Invert"):
            invert = True
        elif value.startswith("Rotate") and "ax10=" in value:
            raw = value.split("ax10=", 1)[1].split()[0]
            try:
                degrees += int(round(int(raw) / 10))
            except ValueError:
                pass
    return invert, degrees % 360


def pixel_spacing_mm(sections: dict) -> list[float]:
    raw = ini_get(sections, "RawImageCreationInfo", "PixelSize")
    if raw:
        try:
            mm = float(raw) / 1_000_000.0
            if mm > 0:
                return [mm, mm]
        except ValueError:
            pass
    dpi = ini_get(sections, "CREATION", "InputDeviceDPI_X") or ini_get(
        sections, "CREATION", "InputDeviceDPI_Y"
    )
    if dpi:
        try:
            return [25.4 / float(dpi), 25.4 / float(dpi)]
        except ValueError:
            pass
    return [0.02513, 0.02513]


def find_xtf(folder: Path, guid: str) -> Path | None:
        
    for name in (f"{guid}.xtf", f"{guid}.XTF"):
        folder_path = Path(folder)
        candidate = folder_path / name
  
        if candidate.is_file():
            return candidate
    return None


def load_pixels(xtf_path: Path, invert: bool, rotation: int) -> np.ndarray:
    image = Image.open(xtf_path)
    if image.mode != "L":
        image = image.convert("L")
    if invert:
        image = ImageOps.invert(image)
    if rotation:
        image = image.rotate(-rotation, expand=True)
    return np.asarray(image, dtype=np.uint8)


def build_dicom(xtf_path: Path, im0_path: Path | None, apply_ops: bool = True) -> bytes:
    sections = parse_ini(im0_path) if im0_path and im0_path.is_file() else {}

    invert, rotation = parse_rotates_and_invert(sections) if apply_ops else (False, 0)
    pixels = load_pixels(xtf_path, invert, rotation)
    rows, cols = pixels.shape

    family = ini_get(sections, "ImageUserData", "PatNName")
    given = ini_get(sections, "ImageUserData", "PatVName")
    patient_name = f"{family} {given}".strip("^") or "ANONYMOUS"
    patient_id = (
        ini_get(sections, "ImageUserData", "PatKNR")
        or ini_get(sections, "ImageUserData", "PatPNR")
        or "UNKNOWN"
    )

    
    
    birth = delphi_to_datetime(ini_get(sections, "ImageUserData", "PatBDate"))
    acquired = delphi_to_datetime(ini_get(sections, "ImageUserData", "ImgCreateDateTime"))
    if acquired is None:
        acquired = datetime.fromtimestamp(xtf_path.stat().st_ctime)

    vorg = ini_get(sections, "ImageUserData", "VorgNr") or xtf_path.stem
    study_key = f"{patient_id}|{acquired.strftime('%Y%m%d')}"
    series_key = f"{study_key}|IO|{ini_get(sections, 'ImageUserData', 'ImgXrayEmitter')}"

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = IO_FOR_PRESENTATION
    file_meta.MediaStorageSOPInstanceUID = uid_from(f"sop|{vorg}")
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = uid_from("dbswin-converter")
    file_meta.ImplementationVersionName = "DBSWIN2DCM"

    

    ds = FileDataset(str(OUTPUT_DIR / f"{vorg}.dcm"), {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    ds.SOPClassUID = IO_FOR_PRESENTATION
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = uid_from(f"study|{study_key}")
    ds.SeriesInstanceUID = uid_from(f"series|{series_key}")
    ds.Modality = "IO"
    ds.ImageType = ["DERIVED", "PRIMARY"]
    ds.ConversionType = "WSD"
    ds.Manufacturer = ini_get(sections, "XRAYEMITTER", "Manufacturer") or "Durr Dental"
    ds.ManufacturerModelName = ini_get(sections, "XRAYEMITTER", "Type") or "VistaScan"
    ds.InstitutionName = ini_get(sections, "ImageUserData", "PracName")
    ds.StationName = ini_get(sections, "CREATION", "MachineName")
    ds.DeviceSerialNumber = ini_get(sections, "RawImageCreationInfo", "SerialNr")
    ds.SeriesDescription = ini_get(sections, "RawImageCreationInfo", "ModeName") or "Intraoral"
    ds.SoftwareVersions = ini_get(sections, "CREATION", "ApplName") or "DBSWIN"

    ds.PatientName = patient_name
    ds.PatientID = patient_id
    # ds.PatientSex = ds.PatientSex = sex if sex in ("M", "F", "O") else ""
    ds.PatientBirthDate = birth.strftime("%Y%m%d") if birth else ""

    ds.StudyDate = acquired.strftime("%Y%m%d")
    ds.SeriesDate = ds.StudyDate
    ds.ContentDate = ds.StudyDate
    ds.StudyTime = acquired.strftime("%H%M%S")
    ds.SeriesTime = ds.StudyTime
    ds.ContentTime = ds.StudyTime
    ds.StudyID = acquired.strftime("%Y%m%d")
    ds.SeriesNumber = "1"
    ds.InstanceNumber = "1"
    ds.AccessionNumber = ""

    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = int(rows)
    ds.Columns = int(cols)
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelSpacing = pixel_spacing_mm(sections)
    ds.PixelData = pixels.tobytes()

    # for testing write file to disk, but don't overwrite existing file save to the same folder of the script
    ds.save_as(
        str(OUTPUT_DIR / "dcm" / f"{vorg}.dcm"),
        write_like_original=False
    )

    buffer = BytesIO()
    pydicom.dcmwrite(buffer, ds, write_like_original=False)
    return buffer.getvalue()


def upload_instance(orthanc_url: str, username: str, password: str, dcm_bytes: bytes) -> dict:
    url = orthanc_url.rstrip("/") + "/instances"
    response = requests.post(
        url,
        data=dcm_bytes,
        auth=(username, password),
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def upload(config, date_from, date_to):
    """
    Find DBSWIN packets in date range, write .dcm next to this script.
    Orthanc upload is left commented until conversion is verified.
    """
    source_path = config["dbswin"]["source_path"]
    
    apply_ops = True  # Apply rotation and inversion from .im0 sidecar
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    file_helper = FileHelper(source_path)
    hits = file_helper.get_file_with_date(date_from, date_to, [".imo"])
    total = len(hits)
    for index,hit in enumerate(hits):
        imo_path = hit["path"]
        folder = hit["folder"]
       
        guid = os.path.splitext(os.path.basename(imo_path))[0]
        
        xtf_path = find_xtf(folder, guid)
       
        if xtf_path is None:
            print(f"SKIP {guid}: no .xtf")
            continue

        im0_path = Path(folder) / f"{guid}.im0"
        
        if not im0_path.is_file():
            print(f"WARN {guid}: no .im0, DICOM will have empty patient tags")
            im0_path = None

        try:
            dcm_bytes = build_dicom(xtf_path, im0_path, apply_ops=apply_ops)
        except Exception as exc:
            print(f"FAIL convert {guid}: {exc}")
            continue


        # Sending to Orthanc — enable after the .dcm files look correct
        orthanc_url = config["orthanc"]["url"]
        username = config["orthanc"]["username"]
        password = config["orthanc"]["password"]
        try:
            info = upload_instance(orthanc_url, username, password, dcm_bytes)
            show_progress(index + 1, total, "Uploading " + str(xtf_path))
        except Exception as exc:
            print(f"FAIL upload {guid}: {exc}")


