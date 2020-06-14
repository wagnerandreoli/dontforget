"""GMail checker. It is not a source nor a target... yet.

Parts of the code below adapted from:
https://github.com/gsuitedevs/python-samples/blob/master/gmail/quickstart/quickstart.py

Copyright 2018 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

New format documentation:
https://developers.google.com/gmail/api/v1/reference

Old format documentation:
https://developers.google.com/resources/api-libraries/documentation/gmail/v1/python/latest/index.html
"""
import logging
import pickle
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Tuple

import click
import rumps
from appdirs import AppDirs
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from ruamel.yaml import YAML
from rumps import notification

from dontforget.app import DontForgetApp
from dontforget.constants import APP_NAME, DELAY
from dontforget.generic import parse_interval

PYTHON_QUICKSTART_URL = "https://developers.google.com/gmail/api/quickstart/python"

# If modifying these scopes, delete the file token.pickle.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GMailPlugin:
    """GMail plugin."""

    class Menu(Enum):
        """Menu items."""

        GMail = "GMail:"
        LastChecked = "Last checked on: Never"

    def init_app(self, app: DontForgetApp) -> bool:
        """Add GMail jobs to the background scheduler.

        :return: True if all GMail accounts were authenticated with OAuth.
        """
        logging.debug("Adding GMail menu")
        app.menu.add(self.Menu.GMail.value)

        all_authenticated = True
        yaml = YAML()
        config_data = yaml.load(app.config_file)
        for data in config_data["gmail"]:
            logging.debug("%s: Creating GMail job", data["email"])
            job = GMailJob(**data)
            if not job.authenticated:
                all_authenticated = False
            else:
                app.scheduler.add_job(job, "interval", misfire_grace_time=10, **job.trigger_args)

            # Add this email to the app menu
            logging.debug("%s: Creating GMail menu", job.gmail.email)
            job_menu = rumps.MenuItem(job.gmail.email)
            job_menu.add(self.Menu.LastChecked.value)
            app.menu.add(job_menu)
            job.menu = job_menu

        app.menu.add(rumps.separator)
        return all_authenticated


class GMailAPI:
    """GMail API wrapper."""

    def __init__(self, email: str) -> None:
        self.email = email.strip()
        config_dir = Path(AppDirs(APP_NAME).user_config_dir)
        self.token_file = config_dir / f"{self.email}-token.pickle"
        self.credentials_file = config_dir / f"{self.email}-credentials.json"

        self.service = None
        self.labels: Dict[str, str] = {}

    def authenticate(self) -> bool:
        """Authenticate using the GMail API.

        The file token.pickle stores the user's access and refresh tokens, and is created automatically when the
        authorization flow completes for the first time.
        """
        from subprocess import run

        if not self.credentials_file.exists():
            click.secho(f"Credential file not found for {self.email}.", fg="bright_red")
            click.echo("Click on the 'Enable the GMail API' button and save the JSON file as ", nl=False)
            click.secho(str(self.credentials_file), fg="green")

            # Open the URL on the browser
            run(["open", f"{PYTHON_QUICKSTART_URL}?email={self.email}"])

            # Open the folder on Finder
            run(["open", str(self.credentials_file.parent)])
            return False

        creds = None
        if self.token_file.exists():
            creds = pickle.load(self.token_file.open("rb"))

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
                creds = flow.run_local_server(port=0, _dummy=self.email)

            # Save the credentials for the next run
            pickle.dump(creds, self.token_file.open("wb"))

        self.service = build("gmail", "v1", credentials=creds)
        return True

    def fetch_labels(self) -> bool:
        """Fetch GMail labels."""
        if not self.service:
            return False
        self.labels = {}
        results = self.service.users().labels().list(userId="me").execute()
        for label in results.get("labels") or []:
            self.labels[label["name"]] = label["id"]
        return True

    def unread_count(self, label_name: str) -> Tuple[int, int]:
        """Return the unread message count (threads and messages) for a label.

        See https://developers.google.com/gmail/api/v1/reference/users/messages/list.

        :return: A tuple with unread thread and unread message count.
        """
        unread_threads = unread_messages = -1
        if self.service and self.labels:
            label_id = self.labels.get(label_name, None)
            if label_id:
                request = self.service.users().messages().list(userId="me", labelIds=[label_id], q="is:unread")
                response = request.execute()

                messages = response.get("messages", [])
                unread_threads = len({msg["threadId"] for msg in messages})
                unread_messages = max([len(messages), response["resultSizeEstimate"]])
        return unread_threads, unread_messages

        # TODO: how to read a single email message
        # for message_dict in response["messages"]:
        #     # https://developers.google.com/gmail/api/v1/reference/users/messages/get#python
        #     result_dict = messages.get(userId="me", id=message_dict["id"], format="full").execute()
        #     parts = result_dict["payload"]["parts"]
        #     for part in parts:
        #         body = base64.urlsafe_b64decode(part["body"]["data"].encode("ASCII"))
        #         print("-" * 50)
        #         pprint(body.decode(), width=200)


class GMailJob:
    """A job to check email on GMail."""

    # TODO: turn this into a source... the "source" concept should be revamped and cleaned.
    #  So many things have to be cleaned/redesigned in this project... it is currently a *huge* pile of mess.
    #  Flask/Docker/Telegram/PyObjC... they are either not needed anymore or they need refactoring to be used again.

    def __init__(self, *, email: str, check: str, labels: Dict[str, str] = None):
        self.gmail = GMailAPI(email)
        self.authenticated = self.gmail.authenticate()
        self.labels_fetched = False
        self.trigger_args = parse_interval(check)
        self.menu: Optional[rumps.MenuItem] = None

        # Add a few seconds of delay before triggering the first request to GMail
        # Configure the optional delay on the config.toml file
        self.trigger_args.update(
            name=f"{self.__class__.__name__}: {email}", start_date=datetime.now() + timedelta(seconds=DELAY)
        )

    def __call__(self, *args, **kwargs):
        """Check GMail for new mail on inbox and specific labels."""
        if not self.labels_fetched:
            self.labels_fetched = self.gmail.fetch_labels()
            self.menu.add(rumps.separator)
            for label in sorted(self.gmail.labels):
                threads, messages = self.gmail.unread_count(label)

                # Only show labels with unread messages
                if threads and messages:
                    # Show unread count of threads and messages for each label
                    self.menu.add(f"{label}: {threads} ({messages})")

        # FIXME: replace this by the actual email check
        values = "GMail", self.gmail.email, "The time is: %s" % datetime.now()
        notification(*values)
        print(*values)
