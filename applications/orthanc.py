def upload(config, date_from, date_to):

    print("=== Orthanc Upload ===")
    print(f"Date From : {date_from}")
    print(f"Date To   : {date_to}")

    orthanc_config = config["orthanc"]

    host = orthanc_config["url"]
    username = orthanc_config["username"]
    password = orthanc_config["password"]

    print(f"Orthanc : {host}")