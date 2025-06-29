import threading,logging,json,time,datetime
import config
import paho.mqtt.client as mqtt
import json

from oven import Oven
log = logging.getLogger(__name__)

# Callback when the client successfully connects to the broker
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to MQTT broker")
    else:
        log.warning(f"MQTT connection failed, return code: {rc}")

# Callback when the client disconnects from the broker
def on_disconnect(client, userdata, rc):
    log.warning(f"Disconnected from MQTT broker (code: {rc}), retrying in 5s...")
    time.sleep(5)
    try:
        client.reconnect()
    except Exception as e:
        log.error(f"Reconnection attempt failed: {e}")


class OvenWatcher(threading.Thread):
    def __init__(self,oven):
        self.last_profile = None
        self.last_log = []
        self.started = None
        self.recording = False
        self.observers = []
        threading.Thread.__init__(self)
        self.daemon = True
        self.oven = oven
        if config.mqtt_enabled:
            self._setup_mqtt()
        self.start()

# FIXME - need to save runs of schedules in near-real-time
# FIXME - this will enable re-start in case of power outage
# FIXME - re-start also requires safety start (pausing at the beginning
# until a temp is reached)
# FIXME - re-start requires a time setting in minutes.  if power has been
# out more than N minutes, don't restart
# FIXME - this should not be done in the Watcher, but in the Oven class


    def _setup_mqtt(self):
        self.client = mqtt.Client()
        self.client.username_pw_set(config.mqtt_user, config.mqtt_pass)
        #self.client.on_connect = on_connect
        #self.client.on_disconnect = on_disconnect
        self.client.connect(config.mqtt_host, config.mqtt_port)
        self.client.loop_start()

    def run(self):
        # Main loop: each iteration handles exceptions internally to stay alive
        while True:
            try:
                oven_state = self.oven.get_state()

                # Save state for new observers if the oven is running
                if oven_state.get("state") == "RUNNING":
                    self.last_log.append(oven_state)
                else:
                    self.recording = False

                # Notify all connected observers via WebSocket
                self.notify_all(oven_state)

                # Publish temperature to MQTT topic
                if config.mqtt_enabled:
                    oven_state["name"] = config.mqtt_kiln_name
                    payload = json.dumps(oven_state)
                    result = self.client.publish(config.mqtt_topic, payload)
                    if result.rc != mqtt.MQTT_ERR_SUCCESS:
                        log.error(f"Publish failed, code: {result.rc}")

            except Exception as exc:
                log.exception(f"Exception in OvenWatcher iteration: {exc}")

            # Sleep for configured time_step, even after exception
            time.sleep(self.oven.time_step)



    def lastlog_subset(self,maxpts=50):
        '''send about maxpts from lastlog by skipping unwanted data'''
        totalpts = len(self.last_log)
        if (totalpts <= maxpts):
            return self.last_log
        every_nth = int(totalpts / (maxpts - 1))
        return self.last_log[::every_nth]

    def record(self, profile):
        self.last_profile = profile
        self.last_log = []
        self.started = datetime.datetime.now()
        self.recording = True
        #we just turned on, add first state for nice graph
        self.last_log.append(self.oven.get_state())

    def add_observer(self,observer):
        if self.last_profile:
            p = {
                "name": self.last_profile.name,
                "data": self.last_profile.data, 
                "type" : "profile"
            }
        else:
            p = None
        
        backlog = {
            'type': "backlog",
            'profile': p,
            'log': self.lastlog_subset(),
            #'started': self.started
        }
        print(backlog)
        backlog_json = json.dumps(backlog)
        try:
            print(backlog_json)
            observer.send(backlog_json)
        except:
            log.error("Could not send backlog to new observer")
        
        self.observers.append(observer)

    def notify_all(self,message):
        message_json = json.dumps(message)
        log.debug("sending to %d clients: %s"%(len(self.observers),message_json))

        for wsock in self.observers:
            if wsock:
                try:
                    wsock.send(message_json)
                except:
                    log.error("could not write to socket %s"%wsock)
                    self.observers.remove(wsock)
            else:
                self.observers.remove(wsock)
