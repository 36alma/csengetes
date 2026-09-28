"""Kozos konfiguracio betoltes/mentes (config.json)."""

from __future__ import annotations

import json
import os
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

class Config():
    def __init__(self) -> None:
        self._configjson = self._load_config()
        self.TIME_MODE = str(self.setvalue("time_mode","ntp"))
        self.ACTIVE_SCHEDULE = str(self.setvalue("active_schedule","normal.json"))
        self.VOLUME_PERCENT = float(self.setvalue("volume_percent",100.0))
        self.DEVICE_INDEX = self.setvalue("device_index",None)
        self.DEVICE_NAME = self.setvalue("device_name",None)
        self.DEFAULT_MEDIA_FILE = str(self.setvalue("default_media_file","csengo.mp3"))
        self.NTP_SERVER = str(self.setvalue("ntp_server","time.google.com"))
        self.MAX_UPLOAD_MB = float(self.setvalue("max_upload_mb",20.0))

    def setvalue(self,key:str,default:Any):
        try:
            return self._configjson[key]
        except KeyError:
            return default
    
    def _load_config(self) -> dict:
        if os.path.isfile(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
        return {}
    
    def save_config(self,) -> None:
        data:dict = {
            "time_mode": self.TIME_MODE,
            "active_schedule": self.ACTIVE_SCHEDULE,
            "volume_percent": self.VOLUME_PERCENT,
            "device_index": self.DEVICE_INDEX,
            "device_name": self.DEVICE_NAME,
            "default_media_file": self.DEFAULT_MEDIA_FILE,
            "ntp_server": self.NTP_SERVER,
            "max_upload_mb": self.MAX_UPLOAD_MB,
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2) 

config = Config()