"""
# MicropyGPS - a GPS NMEA sentence parser for Micropython/Python 3.X
# Copyright (c) 2017 Michael Calvin McCoy (calvin.mccoy@protonmail.com)
# The MIT License (MIT) - see LICENSE file
"""

# TODO:
# Time Since First Fix
# Distance/Time to Target
# More Helper Functions
# Dynamically limit sentences types to parse

from math import floor, modf

# Import utime or time for fix time handling
try:
    # Assume running on MicroPython
    import utime
    get_ticks = utime.ticks_ms
except ImportError:
    # Otherwise default to time module for non-embedded implementations
    # Should still support millisecond resolution.
    import time
    get_ticks = time.time


class MicropyGPS(object):
    """GPS NMEA Sentence Parser. Creates object that stores all relevant GPS data and statistics.
    Parses sentences one character at a time using update(). """

    # Max Number of Characters a valid sentence can be (based on GGA sentence)
    SENTENCE_LIMIT = 90
    __HEMISPHERES = {'N', 'S', 'E', 'W'}
    __NO_FIX = 1
    __FIX_2D = 2
    __FIX_3D = 3
    __DIRECTIONS = ('N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W',
                    'WNW', 'NW', 'NNW')
    __MONTHS = ('January', 'February', 'March', 'April', 'May',
                'June', 'July', 'August', 'September', 'October',
                'November', 'December')
    CLEAR_DATE = (0, 0, 0)
    CLEAR_TIME = (0, 0, 0.0)
    CLEAR_LAT = (0, 0.0, 'N')
    CLEAR_LON = (0, 0.0, 'W')
    __buf = [] # Buffer for MicropyGPS.update()

    def __init__(self, local_offset=0, location_formatting='ddm'):
        """
        Setup GPS Object Status Flags, Internal Data Registers, etc
            local_offset (int): Timzone Difference to UTC
            location_formatting (str): Style For Presenting Longitude/Latitude:
                                       Decimal Degree Minute (ddm) - 40° 26.767′ N
                                       Degrees Minutes Seconds (dms) - 40° 26′ 46″ N
                                       Decimal Degrees (dd) - 40.446°
        """

        #####################
        # Object Status Flags
        self.sentence_active = False
        self.active_segment = 0
        self.process_crc = False
        self.gps_segments = []
        self.crc_xor = 0
        self.char_count = 0
        self.fix_time = 0

        #####################
        # Sentence Statistics
        self.crc_fails = 0
        self.clean_sentences = 0
        self.parsed_sentences = 0

        #####################
        # Logging Related
        self.log_handle = None
        self.log_en = False

        #####################
        # Data From Sentences
        # Time
        self.timestamp = self.CLEAR_TIME
        self.date = self.CLEAR_DATE
        self.local_offset = local_offset

        # Position/Motion
        self._latitude = self.CLEAR_LAT
        self._longitude = self.CLEAR_LON
        self.coord_format = location_formatting
        self.speed = 0.0
        self.course = 0.0
        self.altitude = 0.0
        self.geoid_height = 0.0

        # GPS Info
        self.satellites_in_view = 0
        self.satellites_in_use = 0
        self.satellites_used = []
        self.last_sv_sentence = 0
        self.total_sv_sentences = 0
        self.satellite_data = dict()
        self.hdop = 0.0
        self.pdop = 0.0
        self.vdop = 0.0
        self.valid = False
        self.fix_stat = 0
        self.fix_type = 1

    ########################################
    # Coordinates Translation Functions
    ########################################
    @property
    def latitude(self):
        """Format Latitude Data Correctly"""
        return self.__conv_lat_lon(self._latitude)

    @property
    def longitude(self):
        """Format Longitude Data Correctly"""
        return self.__conv_lat_lon(self._longitude)

    def __conv_lat_lon(self, lat_lon):
        if self.coord_format == 'dd':
            decimal_degrees = lat_lon[0] + (lat_lon[1] / 60)
            sign_dd = (lat_lon[2] in {'N', 'E'}) - (lat_lon[2] in {'S', 'W'})
            return sign_dd * decimal_degrees
        elif self.coord_format == 'dms':
            minute_parts = modf(lat_lon[1])
            seconds = round(minute_parts[0] * 60)
            return (lat_lon[0], int(minute_parts[1]), seconds, lat_lon[2])
        else:
            return lat_lon

    ########################################
    # Logging Related Functions
    ########################################
    def start_logging(self, target_file, mode="append"):
        """
        Create GPS data log object
        """
        # Set Write Mode Overwrite or Append
        mode_code = 'w' if mode == 'new' else 'a'

        try:
            self.log_handle = open(target_file, mode_code)
        except AttributeError:
            print("Invalid FileName")
            return False

        self.log_en = True
        return True

    def stop_logging(self):
        """
        Closes the log file handler and disables further logging
        """
        try:
            self.log_handle.close()
        except AttributeError:
            print("Invalid Handle")
            return False

        self.log_en = False
        return True

    def write_log(self, log_string):
        """Attempts to write the last valid NMEA sentence character to the active file handler
        """
        try:
            self.log_handle.write(log_string)
        except TypeError:
            return False
        return True

    ########################################
    # Sentence Parsers
    ########################################
    def __parse_time(self, utc_string):
        # UTC Timestamp
        try:
            if utc_string:  # Possible timestamp found
                hours = (int(utc_string[0:2]) + self.local_offset) % 24
                minutes = int(utc_string[2:4])
                seconds = float(utc_string[4:])
                if seconds >= 60.0 or minutes >= 60:
                    return False 
                self.timestamp = (hours, minutes, seconds)
            else:  # No Time stamp yet
                self.timestamp = self.CLEAR_TIME
        except ValueError:  # Bad Timestamp value present
            return False
        return True

    def __parse_lat_lon(self, lat, lon):
        try:
            # Latitude
            lat_str, lat_hemi = lat
            if lat_hemi not in self.__HEMISPHERES:
                return False
            lat_degs = int(lat_str[0:2])
            lat_mins = float(lat_str[2:])

            # Longitude
            lon_str, lon_hemi = lon
            if lon_hemi not in self.__HEMISPHERES:
                return False
            lon_degs = int(lon_str[0:3])
            lon_mins = float(lon_str[3:])

            self._latitude = (lat_degs, lat_mins, lat_hemi)
            self._longitude = (lon_degs, lon_mins, lon_hemi)
            return True

        except ValueError:
            return False

    def gprmc(self):
        """Parse Recommended Minimum Specific GPS/Transit data (RMC)Sentence.
        Updates UTC timestamp, latitude, longitude, Course, Speed, Date, and fix status
        """

        # UTC Timestamp
        try:
            utc_string = self.gps_segments[1]
        except IndexError:
            return False
        if not self.__parse_time(utc_string):
            return False

        # Date stamp
        try:
            date_string = self.gps_segments[9]

            # Date string printer function assumes to be year >=2000,
            # date_string() must be supplied with the correct century argument to display correctly
            if date_string:  # Possible date stamp found
                day = int(date_string[0:2])
                month = int(date_string[2:4])
                year = int(date_string[4:6])
                self.date = (day, month, year)
            else:  # No Date stamp yet
                self.date = self.CLEAR_DATE

        except (ValueError, IndexError):  # Bad Date stamp value present
            return False

        # Check Receiver Data Valid Flag
        if self.gps_segments[2] == 'A':  # Data from Receiver is Valid/Has Fix

            # Longitude / Latitude
            try:
                lat_hemi = self.gps_segments[4]
                lat = self.gps_segments[3], lat_hemi
                lon_hemi = self.gps_segments[6]
                lon = self.gps_segments[5], lon_hemi
            except IndexError:
                return False
            parsed = self.__parse_lat_lon(lat, lon)
            if not parsed:
                return False

            # Speed
            try:
                spd_knt = float(self.gps_segments[7])
            except ValueError:
                return False

            # Course
            try:
                if self.gps_segments[8]:
                    course = float(self.gps_segments[8])
                else:
                    course = 0.0
            except ValueError:
                return False

            # TODO - Add Magnetic Variation

            # Update Object Data
            self.speed = spd_knt
            self.course = course
            self.valid = True

            # Update Last Fix Time
            self.new_fix_time()

        else:  # Clear Position Data if Sentence is 'Invalid'
            self._latitude = self.CLEAR_LAT
            self._longitude = self.CLEAR_LON
            self.speed = 0.0
            self.course = 0.0
            self.valid = False

        return True

    def gpgll(self):
        """Parse Geographic Latitude and Longitude (GLL)Sentence. Updates UTC timestamp, latitude,
        longitude, and fix status"""

        # UTC Timestamp
        try:
            utc_string = self.gps_segments[5]
        except IndexError:
            return False
        if not self.__parse_time(utc_string):
            return False

        # Check Receiver Data Valid Flag
        if self.gps_segments[6] == 'A':  # Data from Receiver is Valid/Has Fix

            # Longitude / Latitude
            try:
                lat_hemi = self.gps_segments[2]
                lat = self.gps_segments[1], lat_hemi
                lon_hemi = self.gps_segments[4]
                lon = self.gps_segments[3], lon_hemi
            except IndexError:
                return False
            parsed = self.__parse_lat_lon(lat, lon)
            if not parsed:
                return False

            # Update Object Data
            self.valid = True

            # Update Last Fix Time
            self.new_fix_time()

        else:  # Clear Position Data if Sentence is 'Invalid'
            self._latitude = self.CLEAR_LAT
            self._longitude = self.CLEAR_LON
            self.valid = False

        return True

    def gpvtg(self):
        """Parse Track Made Good and Ground Speed (VTG) Sentence. Updates speed and course"""
        try:
            course = float(self.gps_segments[1]) if self.gps_segments[1] else 0.0
            spd_knt = float(self.gps_segments[5]) if self.gps_segments[5] else 0.0
        except (ValueError, IndexError):
            return False

        # Update Object Data
        self.speed = spd_knt
        self.course = course
        return True

    def gpgga(self):
        """Parse Global Positioning System Fix Data (GGA) Sentence. Updates UTC timestamp, latitude, longitude,
        fix status, satellites in use, Horizontal Dilution of Precision (HDOP), altitude, geoid height and fix status"""

        # UTC Timestamp
        try:
            utc_string = self.gps_segments[1]
        except IndexError:
            return False
        if not self.__parse_time(utc_string):
            return False

        try:
            # Number of Satellites in Use
            satellites_in_use = int(self.gps_segments[7])

            # Get Fix Status
            fix_stat = int(self.gps_segments[6])

        except (ValueError, IndexError):
            return False

        try:
            # Horizontal Dilution of Precision
            hdop = float(self.gps_segments[8])
        except (ValueError, IndexError):
            hdop = 0.0

        # Process Location Data if Fix is GOOD
        if fix_stat:

            # Longitude / Latitude
            try:
                lat_hemi = self.gps_segments[3]
                lat = self.gps_segments[2], lat_hemi
                lon_hemi = self.gps_segments[5]
                lon = self.gps_segments[4], lon_hemi
            except IndexError:
                return False
            parsed = self.__parse_lat_lon(lat, lon)
            if not parsed:
                return False

            # Altitude / Height Above Geoid
            try:
                altitude = float(self.gps_segments[9])
                geoid_height = float(self.gps_segments[11])
            except ValueError:
                altitude = 0.0
                geoid_height = 0.0

            # Update Object Data
            self.altitude = altitude
            self.geoid_height = geoid_height

        # Update Object Data
        self.satellites_in_use = satellites_in_use
        self.hdop = hdop
        self.fix_stat = fix_stat

        # If Fix is GOOD, update fix timestamp
        if fix_stat:
            self.new_fix_time()

        return True

    def gpgsa(self):
        """Parse GNSS DOP and Active Satellites (GSA) sentence. Updates GPS fix type, list of satellites used in
        fix calculation, Position Dilution of Precision (PDOP), Horizontal Dilution of Precision (HDOP), Vertical
        Dilution of Precision, and fix status"""

        # Fix Type (None,2D or 3D)
        try:
            fix_type = int(self.gps_segments[2])
        except ValueError:
            return False

        # Read All (up to 12) Available PRN Satellite Numbers
        sats_used = []
        for sats in range(12):
            sat_number_str = self.gps_segments[3 + sats]
            if sat_number_str:
                try:
                    sat_number = int(sat_number_str)
                    sats_used.append(sat_number)
                except ValueError:
                    return False
            else:
                break

        # PDOP,HDOP,VDOP
        try:
            pdop = float(self.gps_segments[15])
            hdop = float(self.gps_segments[16])
            vdop = float(self.gps_segments[17])
        except ValueError:
            return False

        # Update Object Data
        self.fix_type = fix_type

        # If Fix is GOOD, update fix timestamp
        if fix_type > self.__NO_FIX:
            self.new_fix_time()

        self.satellites_used = sats_used
        self.hdop = hdop
        self.vdop = vdop
        self.pdop = pdop

        return True

    def gpgsv(self):
        """Parse Satellites in View (GSV) sentence. Updates number of SV Sentences,the number of the last SV sentence
        parsed, and data on each satellite present in the sentence"""
        try:
            num_sv_sentences = int(self.gps_segments[1])
            current_sv_sentence = int(self.gps_segments[2])
            sats_in_view = int(self.gps_segments[3])
        except ValueError:
            return False

        # Create a blank dict to store all the satellite data from this sentence in:
        # satellite PRN is key, tuple containing telemetry is value
        satellite_dict = dict()

        # Calculate  Number of Satelites to pull data for and thus how many segment positions to read
        if num_sv_sentences == current_sv_sentence:
            # Last sentence may have 1-4 satellites; 5 - 20 positions
            sat_segment_limit = (sats_in_view - ((num_sv_sentences - 1) * 4)) * 5
        else:
            sat_segment_limit = 20  # Non-last sentences have 4 satellites and thus read up to position 20

        # Try to recover data for up to 4 satellites in sentence
        for sats in range(4, sat_segment_limit, 4):

            try:
                # If no PRN is found, then the sentence has no more satellites to read
                if not self.gps_segments[sats]:
                   break
            except IndexError:
                return False

            # If a PRN is present, grab satellite data
            try:
                sat_id = int(self.gps_segments[sats])
            except (ValueError,IndexError):
                return False

            try:  # elevation can be null (no value) when not tracking
                elevation = int(self.gps_segments[sats+1])
            except (ValueError,IndexError):
                elevation = None

            try:  # azimuth can be null (no value) when not tracking
                azimuth = int(self.gps_segments[sats+2])
            except (ValueError,IndexError):
                azimuth = None

            try:  # SNR can be null (no value) when not tracking
                snr = int(self.gps_segments[sats+3])
            except (ValueError,IndexError):
                snr = None
            # Add Satellite Data to Sentence Dict
            satellite_dict[sat_id] = (elevation, azimuth, snr)


        # Update Object Data
        self.total_sv_sentences = num_sv_sentences
        self.last_sv_sentence = current_sv_sentence
        self.satellites_in_view = sats_in_view

        # For a new set of sentences, we either clear out the existing sat data or
        # update it as additional SV sentences are parsed
        if current_sv_sentence == 1:
            self.satellite_data = satellite_dict
        else:
            self.satellite_data.update(satellite_dict)

        return True

    ##########################################
    # Data Stream Handler Functions
    ##########################################

    def new_sentence(self):
        """Adjust Object Flags in Preparation for a New Sentence"""
        self.gps_segments = ['']
        self.active_segment = 0
        self.crc_xor = 0
        self.sentence_active = True
        self.process_crc = True
        self.char_count = 0
        self.__buf = []

    def __update_segment(self):
        self.gps_segments[self.active_segment] = ''.join(self.__buf)
        self.__buf = []

    def update(self, new_char):
        """Process a new input char and updates GPS object if necessary based on special characters ('$', ',', '*')
        Function builds a list of received string that are validate by CRC prior to parsing by the  appropriate
        sentence function. Returns sentence type on successful parse, None otherwise"""

        valid_sentence = False

        # Validate new_char is a printable char
        ascii_char = ord(new_char)

        if 20 <= ascii_char <= 126:
        #if 10 <= ascii_char <= 126: # cf. LF := 0x0a = 10, CR := 0x0d = 13.
            self.char_count += 1

            # Write Character to log file if enabled
            if self.log_en:
                self.write_log(new_char)

            # Check if a new string is starting ($)
            if new_char == '$':
                self.new_sentence()
                return None

            elif self.sentence_active:

                # Check if sentence is ending (*)
                if new_char == '*':
                    self.process_crc = False
                    self.__update_segment()
                    self.active_segment += 1
                    self.gps_segments.append('')
                    return None

                # Check if a section is ended (,), Create a new substring to feed
                # characters to
                elif new_char == ',':
                    self.__update_segment()
                    self.active_segment += 1
                    self.gps_segments.append('')

                # Store All Other printable character and check CRC when ready
                else:
                    self.__buf.append(new_char)

                    # When CRC input is disabled, sentence is nearly complete
                    if not self.process_crc:

                        if len(self.__buf) == 2:
                            self.__update_segment()
                            try:
                                final_crc = int(self.gps_segments[self.active_segment], 16)
                                if self.crc_xor == final_crc:
                                    valid_sentence = True
                                else:
                                    self.crc_fails += 1
                            except ValueError:
                                pass  # CRC Value was deformed and could not have been correct

                # Update CRC
                if self.process_crc:
                    self.crc_xor ^= ascii_char

                # If a Valid Sentence Was received and it's a supported sentence, then parse it!!
                if valid_sentence:
                    self.clean_sentences += 1  # Increment clean sentences received
                    self.sentence_active = False  # Clear Active Processing Flag

                    if self.gps_segments[0] in self.supported_sentences:

                        # parse the Sentence Based on the message type, return True if parse is clean
                        if self.supported_sentences[self.gps_segments[0]](self):

                            # Let host know that the GPS object was updated by returning parsed sentence type
                            self.parsed_sentences += 1
                            return self.gps_segments[0]

                # Check that the sentence buffer isn't filling up with Garage waiting for the sentence to complete
                if self.char_count > self.SENTENCE_LIMIT:
                    self.sentence_active = False

        # Tell Host no new sentence was parsed
        return None

    def new_fix_time(self):
        """Updates a high resolution counter with current time when fix is updated. Currently only triggered from
        GGA, GSA and RMC sentences"""
        self.fix_time = get_ticks()

    #########################################
    # User Helper Functions
    # These functions make working with the GPS object data easier
    #########################################

    def satellite_data_updated(self):
        """
        Checks if the all the GSV sentences in a group have been read, making satellite data complete
        :return: boolean
        """
        if self.total_sv_sentences > 0 and self.total_sv_sentences == self.last_sv_sentence:
            return True
        else:
            return False

    def unset_satellite_data_updated(self):
        """
        Mark GSV sentences as read indicating the data has been used and future updates are fresh
        """
        self.last_sv_sentence = 0

    def satellites_visible(self):
        """
        Returns a list of of the satellite PRNs currently visible to the receiver
        :return: list
        """
        return list(self.satellite_data.keys())

    def time_since_fix(self):
        """Returns number of millisecond since the last sentence with a valid fix was parsed. Returns 0 if
        no fix has been found"""

        # Test if a Fix has been found
        if self.fix_time == 0:
            return -1

        # Try calculating fix time using utime; if not running MicroPython
        # time.time() returns a floating point value in secs
        try:
            current = utime.ticks_diff(get_ticks(), self.fix_time)
        except NameError:
            current = (get_ticks() - self.fix_time) * 1000  # ms

        return current

    def compass_direction(self):
        """
        Determine a cardinal or inter-cardinal direction based on current course.
        :return: string
        """
        # Calculate the offset for a rotated compass
        offset_course = (self.course + 11.25) % 360.0

        # Each compass point is separated by 22.5 degrees, divide to find lookup value
        dir_index = floor(offset_course / 22.5)

        return self.__DIRECTIONS[dir_index]

    def __pp_lat_lon(self, lat_lon):
        """
        Prettify latitude/longitude strings.
        :return: string
        """
        deg, min, sec = '°', "'", '"'
        if self.coord_format == 'dd':
            return f'{lat_lon}{deg}'
        elif self.coord_format == 'dms':
            d, m, s, hemi = lat_lon
            return f'{d}{deg} {m}{min} {s}{sec} {hemi}'
        else:
            d, dm, hemi = lat_lon
            return f'{d}{deg} {dm}{min} {hemi}'

    def latitude_string(self):
        """
        Create a readable string of the current latitude data
        :return: string
        """
        return self.__pp_lat_lon(self.latitude)

    def longitude_string(self):
        """
        Create a readable string of the current longitude data
        :return: string
        """
        return self.__pp_lat_lon(self.longitude)

    def speed_string(self, unit='kph'):
        """
        Creates a readable string of the current speed data in one of three units
        :param unit: string of 'kph','mph, or 'knot'
        :return: string
        """
        if unit == 'mph':
            spd = self.speed * 1.151

        elif unit == 'knot':
            if self.speed != 1:
                unit = 'knots'
            spd = self.speed

        else:
            unit = 'km/h'
            spd = self.speed * 1.852

        return f'{spd} {unit}'

    def date_string(self, formatting='s_mdy', century='20'):
        """
        Creates a readable string of the current date.
        Can select between long format: Januray 1st, 2014
        or two short formats:
        11/01/2014 (MM/DD/YYYY)
        01/11/2014 (DD/MM/YYYY)
        :param formatting: string 's_mdy', 's_dmy', or 'long'
        :param century: int(or str) delineating the century the GPS data is from (19 for 19XX, 20 for 20XX)
        :return: date_string  string with long or short format date
        """

        # Long Format Januray 1st, 2014
        if formatting == 'long':
            # Retrieve Month string from private set
            month = self.__MONTHS[self.date[1] - 1]

            # Determine Date Suffix
            st_nd_rd = {1:'st', 21:'st', 31:'st', 2:'nd', 22:'nd', 3:'rd', 23:'rd'}
            if self.date[0] in st_nd_rd:
                suffix = st_nd_rd[self.date[0]]
            else:
                suffix = 'th'

            day = f'{self.date[0]}{suffix}'  # Create Day String

            year = f'{century}{self.date[2]}'  # Create Year String

            date_string = f'{month} {day}, {year}'  # Put it all together

        else:
            # Add leading zeros to day string if necessary
            day = f'{self.date[0]:02d}'

            # Add leading zeros to month string if necessary
            month = f'{self.date[1]:02d}'

            # Add leading zeros to year string if necessary
            year = f'{century}{self.date[2]:02d}'

            # Build final string based on desired formatting
            if formatting == 's_dmy':
                date_string = f'{day}/{month}/{year}'

            else:  # Default date format
                date_string = f'{month}/{day}/{year}'

        return date_string

    # All the currently supported NMEA sentences
    supported_sentences = {'GPRMC': gprmc, 'GLRMC': gprmc,
                           'GPGGA': gpgga, 'GLGGA': gpgga,
                           'GPVTG': gpvtg, 'GLVTG': gpvtg,
                           'GPGSA': gpgsa, 'GLGSA': gpgsa,
                           'GPGSV': gpgsv, 'GLGSV': gpgsv,
                           'GPGLL': gpgll, 'GLGLL': gpgll,
                           'GNGGA': gpgga, 'GNRMC': gprmc,
                           'GNVTG': gpvtg, 'GNGLL': gpgll,
                           'GNGSA': gpgsa,
                          }

if __name__ == "__main__":
    pass
