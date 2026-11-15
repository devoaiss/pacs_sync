import requests


def upload_to_orthanc(
    orthanc_url,
    username,
    password,
    dicom_bytes
):
    try:
        response = requests.post(
            f"{orthanc_url.rstrip('/')}/instances",
            data=dicom_bytes,
            auth=(username, password),
            headers={
                "Content-Type": "application/dicom"
            },
            timeout=120
        )

        response.raise_for_status()

        result = response.json()

        

        return result

    except requests.exceptions.RequestException as e:
        print(f"Orthanc upload error: {e}")
        return None

    except Exception as e:
        print(f"Unexpected Orthanc error: {e}")
        return None