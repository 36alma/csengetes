from enum import Flag
class MixerStatus(Flag):
    IDLE = 0
    PLAYING_MUSIC = 0b001
    PLAYING_MIC = 0b010
    MIXING = 0b011
    BELL = 0b100
    