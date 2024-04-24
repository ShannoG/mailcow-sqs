import ssl
import time
import os
import datetime
import email
import smtplib
import threading
import traceback
import re
import base64
import quopri
import signal
import logging
import sys
import socket
import json

import imapclient
import configparser

import boto3

class Getmail(threading.Thread):
   
    def __init__(self, configparser_file, config_name):
        threading.Thread.__init__(self)
        self.event = threading.Event()
        #self.configparser_file = configparser_file
        #self.config_name = config_name
        #self.setName("Thread-%s" % config_name)
        self.name = "Thread-%s" % config_name
        self.imap = None
        self.exit_sqs_loop = False
        self.exception_counter = 0
        self.print_lock = threading.Lock()

        self.lmtp_hostname    = configparser_file.get(       config_name, 'lmtp_hostname')
        self.lmtp_port        = configparser_file.getint(    config_name, 'lmtp_port')
        self.lmtp_debug       = configparser_file.getboolean(config_name, 'lmtp_debug')

        self.sqs_queue_url = configparser_file.get(config_name, 'sqs_queue_url')
        self.sqs_queue_wait_time_seconds = configparser_file.getint(config_name, 'sqs_queue_wait_time_seconds')  
    #get the region from IMDS using boto utils
    aws_region = "ap-southeast-2"
    sqs = boto3.client('sqs', region_name=aws_region)
    s3 = boto3.client('s3', region_name=aws_region)
    ses = boto3.client('ses', region_name=aws_region)


    def run(self):
        while not self.exit_sqs_loop:
          try: 
            self.event.wait(5)
            self.receive_sqs_messages()
          except Exception as e:
            logging.error("ERROR: %s" % (e))
            #traceback.print_exc()
          
          if not self.exit_sqs_loop:        
            self.exception_counter += 1
            logging.error("ERROR: restart thread in %s minutes (counter: %d)" % (self.exception_counter * self.exception_counter, self.exception_counter))
            self.event.wait(60 * self.exception_counter * self.exception_counter )

    def receive_sqs_messages(self):
        while not self.exit_sqs_loop:
          try:
            response = self.sqs.receive_message(
                QueueUrl=self.sqs_queue_url,
                AttributeNames=[
                    'SentTimestamp'
                ],
                MaxNumberOfMessages=1,
                MessageAttributeNames=[
                    'All'
                ],
                VisibilityTimeout=0,
                WaitTimeSeconds=self.sqs_queue_wait_time_seconds
            )
            if 'Messages' in response:
              for message in response['Messages']:
                logging.info("Received message: %s" % (message['MessageId']))
                self.process_sqs_message(message)
                #logging.info("Deleting message: %s" % (receipt_handle))
                #self.sqs.delete_message(
          except Exception as e:
            logging.error("Recieve SQS (Exception - send_message): %s" % (e))
            return False



    def process_sqs_message(self, message):
        logging.info("Process SQS message: %s" % (message['MessageId']))
        message_body = json.loads(message['Body'])
        #logging.info("Got a message body: %s" % (message_body))
        message_destination = message_body['mail']['destination']
        #logging.info("Got a message destination: %s" % (message_destination))
        # Fetch the object from s3
        s3_object = self.s3.get_object(Bucket=message_body['receipt']['action']['bucketName'], Key=message_body['receipt']['action']['objectKey'])
        logging.info("Create the email from s3 object...")
        email_message = email.message_from_bytes(s3_object['Body'].read())
        logging.info("Created the email from s3 object...")
        if self.lmtp_deliver_sqs_mail(email_message, message_destination):
          logging.info("Delete SQS message: %s" % (message))
          self.sqs.delete_message(QueueUrl=self.sqs_queue_url, ReceiptHandle=message['ReceiptHandle'])

    def lmtp_deliver_sqs_mail(self, email_message, message_destination):
        logging.info( "LMTP deliver: start -- LMTP host: %s:%s" % (self.lmtp_hostname, self.lmtp_port))
        try: 
         
          try:
            lmtp = smtplib.LMTP(self.lmtp_hostname, self.lmtp_port)
          except ConnectionRefusedError as e:
            logging.error("LMTP deliver (ConnectionRefusedError): %s" % (e))
            return False
          except socket.gaierror as e:
            logging.error("LMTP deliver (LMTP-Server is not reachable): %s" % (e))  
            return False  
          except smtplib.SMTPRecipientsRefused as e:
            logging.error("LMTP deliver (SMTPRecipientsRefused): %s" % (e))
            logging.info("LMTP server rejected the recipient addresses. Raising a bounce")
            # self.ses.send_bounce(
            #     OriginalMessageId=email_message['Message-ID'],
            #     BounceSender="bounces@shanmtb.com",
            #     #Explanation='LMTP server rejected the recipient addresses. Raising a bounce',
            #     # this exception includes a "recipient" dict which contains information about the rejected recipient(s)
            #     BouncedRecipientInfoList=

            # )
            return False

          if self.lmtp_debug:
            lmtp.set_debuglevel(1)

          try:
            lmtp.send_message(email_message, to_addrs=message_destination)
          except Exception as e:
            logging.error("LMTP deliver (Exception - send_message): %s" % (e))
            return False
                 
            #return False
          finally:
            logging.info( "LMTP deliver: end -- LMTP host: %s:%s" % (self.lmtp_hostname, self.lmtp_port))
            lmtp.quit()
        except Exception as e:
          logging.error("LMTP deliver (Exception): %s" % (e))
          logging.error(traceback.format_exc())
          return False
        return True

########################################################################################################################
########################################################################################################################
########################################################################################################################


def start_getmail():

  configparser_file = get_configparser_file()
  all_connections = {}

  for config_name in configparser_file.sections():
        all_connections[config_name] = Getmail(configparser_file, config_name)
        all_connections[config_name].start()  

  try: 
    exit_program = False
    while not exit_program:
      try:
        signal.pause()
      except KeyboardInterrupt:
        exit_program = True
      except Exception as e:
        logging.error("ERROR: %s" % (e))
        traceback.print_exc()
  finally:
    logging.info("START: shutdown all IMAP connections")
    for config_name in all_connections:
      all_connections[config_name].imap_idle_stop()
    for config_name in all_connections:
      all_connections[config_name].join()
    logging.info("END: shutdown all IMAP connections")


def get_configparser_file():

  if os.path.isfile("./settings.ini"):
    config_file_path = "./settings.ini"
  else:
    logging.error("ERROR settings.ini not found!")
    return

  logging.info("use config file: %s" % config_file_path)
  configparser_file = configparser.ConfigParser(interpolation=None)
  configparser_file.read([os.path.abspath(config_file_path)])

  return configparser_file

def exit_gracefully(signum, frame):
    logging.info("Caught signal %d" % signum)
    raise KeyboardInterrupt
        
if __name__ == "__main__":
    signal.signal(signal.SIGINT,  exit_gracefully)
    signal.signal(signal.SIGTERM, exit_gracefully)

    logging.basicConfig(
      format='%(asctime)s - %(threadName)s - %(levelname)s: %(message)s',
      level=logging.INFO
    )

    start_getmail()
