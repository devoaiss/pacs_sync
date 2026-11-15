from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("mysql.connector")

hiddenimports += [
    "mysql.connector.plugins.mysql_native_password",
    "mysql.connector.plugins.caching_sha2_password",
    "mysql.connector.plugins.sha256_password",
    "mysql.connector.plugins.mysql_clear_password",
]