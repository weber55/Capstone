import os
import datetime
import time
from time import sleep
import MySQLdb
from firebase import firebase
import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
from pypozyx import (POZYX_POS_ALG_UWB_ONLY, POZYX_3D, Coordinates, POZYX_SUCCESS, POZYX_ANCHOR_SEL_AUTO,
                     DeviceCoordinates, PozyxSerial, get_first_pozyx_serial_port, SingleRegister, DeviceList)
from pythonosc.udp_client import SimpleUDPClient
firebase = firebase.FirebaseApplication('https://capstone-ce683.firebaseio.com/', None)

onLEDPin = 23
offLEDPin = 18
buttonPin = 17
GPIO.setup(onLEDPin, GPIO.OUT)
GPIO.setup(offLEDPin, GPIO.OUT)
GPIO.setup(buttonPin, GPIO.IN, pull_up_down = GPIO.PUD_UP)
GPIO.output(offLEDPin, GPIO.HIGH)
GPIO.output(onLEDPin, GPIO.LOW)
global c
global db
inUse = 0
x = 0
y = 0
Id = 0

#ReadyToLocalize Pozyx class
class ReadyToLocalize(object):
    """Continuously calls the Pozyx positioning function and prints its position."""

    def __init__(self, pozyx, osc_udp_client, anchors, algorithm=POZYX_POS_ALG_UWB_ONLY, dimension=POZYX_3D, height=1000, remote_id=None):
        self.pozyx = pozyx
        self.osc_udp_client = osc_udp_client

        self.anchors = anchors
        self.algorithm = algorithm
        self.dimension = dimension
        self.height = height
        self.remote_id = remote_id

    def setup(self):
        """Sets up the Pozyx for positioning by calibrating its anchor list."""
        self.pozyx.clearDevices(self.remote_id)
        self.setAnchorsManual()
        self.printPublishConfigurationResult()

    def loop(self):
        """Performs positioning and displays/exports the results."""
        position = Coordinates()
        status = self.pozyx.doPositioning(
            position, self.dimension, self.height, self.algorithm, remote_id=self.remote_id)
        if status == POZYX_SUCCESS:
            self.printPublishPosition(position)
        else:
            self.printPublishErrorCode("positioning")

    def printPublishPosition(self, position):
        """Prints the Pozyx's position and possibly sends it as a OSC packet"""
        global x
        global y
        global Id
        network_id = self.remote_id
        if network_id is None:
            network_id = 0x4D2
            Id = int(network_id)
        if self.osc_udp_client is not None:
            self.osc_udp_client.send_message(
                "/position", [network_id, int(position.x), int(position.y), int(position.z)])
            x = int(position.x)
            y = int(position.y)

    def printPublishErrorCode(self, operation):
        """Prints the Pozyx's error and possibly sends it as a OSC packet"""
        error_code = SingleRegister()
        network_id = self.remote_id
        if network_id is None:
            self.pozyx.getErrorCode(error_code)
            print("LOCAL ERROR %s, %s" % (operation, self.pozyx.getErrorMessage(error_code)))
            if self.osc_udp_client is not None:
                self.osc_udp_client.send_message("/error", [operation, 0, error_code[0]])
            return
        status = self.pozyx.getErrorCode(error_code, self.remote_id)
        if status == POZYX_SUCCESS:
            print("ERROR %s on ID %s, %s" %
                  (operation, "0x%0.4x" % network_id, self.pozyx.getErrorMessage(error_code)))
            if self.osc_udp_client is not None:
                self.osc_udp_client.send_message(
                    "/error", [operation, network_id, error_code[0]])
        else:
            self.pozyx.getErrorCode(error_code)
            print("ERROR %s, couldn't retrieve remote error code, LOCAL ERROR %s" %
                  (operation, self.pozyx.getErrorMessage(error_code)))
            if self.osc_udp_client is not None:
                self.osc_udp_client.send_message("/error", [operation, 0, -1])
            # should only happen when not being able to communicate with a remote Pozyx.

    def setAnchorsManual(self):
        """Adds the manually measured anchors to the Pozyx's device list one for one."""
        status = self.pozyx.clearDevices(self.remote_id)
        for anchor in self.anchors:
            status &= self.pozyx.addDevice(anchor, self.remote_id)
        if len(self.anchors) > 4:
            status &= self.pozyx.setSelectionOfAnchors(POZYX_ANCHOR_SEL_AUTO, len(self.anchors))
        return status

    def printPublishConfigurationResult(self):
        """Prints and potentially publishes the anchor configuration result in a human-readable way."""
        list_size = SingleRegister()
        self.pozyx.getDeviceListSize(list_size, self.remote_id)
        if list_size[0] != len(self.anchors):
            self.printPublishErrorCode("configuration")
            return
        device_list = DeviceList(list_size=list_size[0])
        self.pozyx.getDeviceIds(device_list, self.remote_id)
        for i in range(list_size[0]):
            anchor_coordinates = Coordinates()
            self.pozyx.getDeviceCoordinates(device_list[i], anchor_coordinates, self.remote_id)
            if self.osc_udp_client is not None:
                self.osc_udp_client.send_message(
                    "/anchor", [device_list[i], int(anchor_coordinates.x), int(anchor_coordinates.y), int(anchor_coordinates.z)])
                sleep(0.025)

    def printPublishAnchorConfiguration(self):
        """Prints and potentially publishes the anchor configuration"""
        for anchor in self.anchors:
            if self.osc_udp_client is not None:
                self.osc_udp_client.send_message(
                    "/anchor", [anchor.network_id, int(anchor.coordinates.x), int(anchor.coordinates.y), int(anchor.coordinates.z)])
                sleep(0.025)
#ReadyToLocalize Pozyx class

def format_time():
    dTime = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return dTime

def in_use():
    global inUse
    if (inUse == 0):
        inUse = 1
        GPIO.output(offLEDPin, GPIO.LOW)
        GPIO.output(onLEDPin, GPIO.HIGH)
    else:
        inUse = 0
        GPIO.output(offLEDPin, GPIO.HIGH)
        GPIO.output(onLEDPin, GPIO.LOW)

def insert_to_db():
    global Id
    global x
    global y
    maintenance = format_time()
    global inUse
    print('Insert -- ID: ', Id, '| x: ', x, '| y: ', y, '| Use: ', inUse, '| Maintenance: ', maintenance)
    sql = "INSERT INTO Ventilator (ID, X, Y, InUse, Maintenance) VALUES (%d, %f, %f, %s, '%s')"
    try:   
        c.execute(sql %(Id, x, y, inUse, maintenance))
        db.commit()
    except:
        sql2 = "REPLACE INTO Ventilator (ID, X, Y, InUse, Maintenance) VALUES (%d, %f, %f, %s, '%s')"
        c.execute(sql2 %(Id, x, y, inUse, maintenance))
        db.commit()
    else:
        print('Insert Error')
        db.rollback()
    
def read_from_db():
    try:
        c.execute("SELECT * FROM Ventilator ORDER BY ID DESC LIMIT 1")
        result = c.fetchone()
        if result is not None:
            print('Read   -- ID: ', result[0], '| x: ', result[1], '| Y: ', result[2], '| Use: ', result[3], '| Maintenance: ', result[4])
    except:
        print('read error')
        
def main():
    
    serial_port = get_first_pozyx_serial_port()
    if serial_port is None:
        print("No Pozyx connected. Check your USB cable or your driver!")
        quit()
    remote_id = 0x1234                 # remote device network ID
    remote = False                     # whether to use a remote device
    if not remote:
        remote_id = None
    use_processing = True              # enable to send position data through OSC
    ip = "127.0.0.1"                   # IP for the OSC UDP
    network_port = 8888                # network port for the OSC UDP
    osc_udp_client = None
    if use_processing:
        osc_udp_client = SimpleUDPClient(ip, network_port)
    anchors = [DeviceCoordinates(0x6E2A, 1, Coordinates(0, 0, 3175)),
               DeviceCoordinates(0x6E0E, 1, Coordinates(0, 4114, 3175)),
               DeviceCoordinates(0x697F, 1, Coordinates(3429, 0, 3175)),
               DeviceCoordinates(0x6E6F, 1, Coordinates(3429, 4114, 3175))]
    algorithm = POZYX_POS_ALG_UWB_ONLY  # positioning algorithm to use
    dimension = POZYX_3D                # positioning dimension
    height = 1000                       # height of device, required in 2.5D positioning
    pozyx = PozyxSerial(serial_port)
    r = ReadyToLocalize(pozyx, osc_udp_client, anchors, algorithm, dimension, height, remote_id)
    r.setup()
    
    while 1:
        print()
        r.loop()
        if not GPIO.input(buttonPin):
            in_use()
        insert_to_db()
        read_from_db()
        maintenance = format_time()
        data = {"ID": Id, "X": x, "Y": y, "InUse": inUse, "Maintenance": maintenance}
        firebase.post('/Ventilator', data)
        time.sleep(2)
        
if __name__=='__main__':
    try:
        #35.225.129.78
        db = MySQLdb.connect(host="localhost", user="root", passwd="password", db="PI_COORDINATES")
        c = db.cursor()
    except:
        print('main error')  
    try:
        main()
    except KeyboardInterrupt:
        GPIO.cleanup()
        print ('Goodbye')
        pass
