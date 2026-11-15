import argparse
import importlib
import pkgutil
import sys
from datetime import datetime
import sys
import applications
from core import config
from core.config import load_config
import mysql.connector.plugins.mysql_native_password

LOG_FILE = "./logs.txt"


def write_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def discover_applications():

    apps = {}

    for module_info in pkgutil.iter_modules(applications.__path__):

        module_name = module_info.name

        # Ignore private modules
        if module_name.startswith("_"):
            continue

        module = importlib.import_module(
            f"applications.{module_name}"
        )

        apps[module_name] = module

    return apps


def create_parser(applications):

    parser = argparse.ArgumentParser(
        prog="pacs_uploader",
        description="PACS synchronization utility"
    )

    parser.add_argument(
        "application",
        choices=applications.keys(),
        help="Application to process"
    )

    parser.add_argument(
        "action",
        choices=["upload"],
        help="Action to perform"
    )

    parser.add_argument(
        "--date-from",
        required=True,
        help="Start date (YYYY-MM-DD)"
    )

    parser.add_argument(
        "--date-to",
        required=True,
        help="End date (YYYY-MM-DD)"
    )

    parser.add_argument(
            "--config-path",
            required=False,
            help="Path to the configuration file"
    )

    return parser


def main():
    write_log("START")


    # Discover applications
    apps = discover_applications()



    if not apps:
        print("ERROR: No applications found.")
        write_log("ERROR: No applications found.")
        sys.exit(1)

    # Create CLI
    parser = create_parser(apps)



    args = parser.parse_args()



    # Load configuration
    # check if args.config_path is provided, if not use default config.ini
    if hasattr(args, 'config_path') and args.config_path:
      
        config = load_config(path=args.config_path)

    else:
        config = load_config()

  
    # Get requested application
    application = apps[args.application]


    # --------------------------------------------------
    # Execute
    # --------------------------------------------------

    if args.action == "upload":

        if not hasattr(application, "upload"):
            raise RuntimeError(
                f"Application '{args.application}' "
                f"does not implement upload()"
            )
     
        # check config args.application


        if not config[args.application]:
            raise ValueError(f"Configuration for '{args.application}' is missing.")

        

        
        application.upload(
            config=config,
            date_from=args.date_from,
            date_to=args.date_to
        )

        write_log(f'{args} upload completed successfully.')


if __name__ == "__main__":

    try:
        main()
        write_log("END - Successfully completed")
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        write_log("ERROR: Operation cancelled.")
        sys.exit(1)

    except Exception as e:
        print(f"ERROR: {e}")
        write_log(f"ERROR: {e}")
        sys.exit(1)