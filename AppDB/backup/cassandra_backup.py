""" Cassandra data backup. """

import logging
import os
import sys
import tarfile
import subprocess
from subprocess import call
from subprocess import CalledProcessError

import backup_exceptions
import gcs_helper
from backup_recovery_constants import StorageTypes

sys.path.append(os.path.join(os.path.dirname(__file__), "../"))
import dbconstants

sys.path.append(os.path.join(os.path.dirname(__file__), "../../lib/"))
import constants

sys.path.append(os.path.join(os.path.dirname(__file__), "../cassandra/"))
import start_cassandra
import shut_down_cassandra
from cassandra_interface import NODE_TOOL
from cassandra_interface import KEYSPACE

# Location where we place the tar of the snapshot.
BACKUP_DIR_LOCATION = "/opt/appscale/backups"

# File location of where the latest backup goes.
BACKUP_FILE_LOCATION = "{0}/cassandra_backup.tar.gz".format(BACKUP_DIR_LOCATION)

# Cassandra directories to remove to get rid of data.
CASSANDRA_DATA_SUBDIRS = ["Keyspace1", "system",
  # "commitlog", "saved_caches",
  # "system_traces"
]

def clear_old_snapshots():
  """ Remove any old snapshots to minimize diskspace usage both locally. """
  logging.info('Removing old Cassandra snapshots...')
  try:
    call([NODE_TOOL, 'clearsnapshot'])
  except CalledProcessError as error:
    logging.error('Error while deleting old Cassandra snapshots. Error: {0}'.\
      format(str(error)))

def create_snapshot():
  """ Perform local Cassandra backup by taking a new snapshot.

  Returns:
    True on success, False otherwise.
  """
  logging.info('Creating new Cassandra snapshots...')
  try:
    call([NODE_TOOL, 'snapshot'])
  except CalledProcessError as error:
    logging.error('Error while creating new Cassandra snapshots. Error: {0}'.\
      format(str(error)))
    return False
  return True

def get_cassandra_snapshot_file_names():
  """ Yields all file names which should be tar'ed up.

  Returns:
    A list of files.
  """
  file_list = []
  data_dir = "{0}{1}".format(constants.APPSCALE_DATA_DIR, "cassandra")
  for full_path, _, _ in os.walk(data_dir):
    if 'snapshots' in full_path:
      file_list.append(full_path)

  logging.info("List of snapshot paths: {0}".format(file_list))
  return file_list

def tar_backup_files(file_paths):
  """ Tars all snapshot files for a given snapshot name.

  Args:
    file_paths: A list of files to tar up.
  Returns:
    The path to the tar file, None otherwise.
  """

  # Create backups dir if not there.
  try:
    call(["mkdir", "-p", BACKUP_DIR_LOCATION])
  except CalledProcessError as error:
    logging.error("Error while creating dir '{0}'. Error: {1}".
      format(BACKUP_DIR_LOCATION, str(error)))
    return None

  backup_file_location = BACKUP_FILE_LOCATION

  # Delete previous backup.
  try:
    call(["rm", "-f", backup_file_location])
  except CalledProcessError as error:
    logging.error("Error while deleting previous backup '{0}'. Error: {1}".
      format(backup_file_location, str(error)))
    backup_file_location += '_latest'

  tar = tarfile.open(backup_file_location, "w:gz")
  for name in file_paths:
    tar.add(name)
  tar.close()

  return backup_file_location

def backup_data(storage, path=''):
  """ Backup Cassandra snapshot data directories/files. 
  
  Args:
    storage: A str, the storage that is used for storing the backup.
    path: A str, the full backup filename path to use for cloud backup.
  Returns:
    The path to the backup file on success, None otherwise.
  """
  logging.info("Starting new db backup.")
  clear_old_snapshots()

  if not create_snapshot():
    logging.error("Failed to create Cassandra snapshots. Aborting backup...")
    return None

  files = get_cassandra_snapshot_file_names()
  if not files:
    logging.error("No Cassandra files were found to tar up. Aborting backup...")
    return None

  tar_file = tar_backup_files(files)
  if not tar_file:
    logging.error('Error while tarring up snapshot files. Aborting backup...')
    remove_local_backup_file(tar_file)
    return None

  if storage == StorageTypes.LOCAL_FS:
    logging.info("Done with local db backup!")
    return tar_file
  elif storage == StorageTypes.GCS:
    return_value = path
    # Upload to GCS.
    if not gcs_helper.upload_to_bucket(path, tar_file):
      logging.error("Upload to GCS failed. Aborting backup...")
      return_value = None
    else:
      logging.info("Done with db backup!")

    # Remove local backup file.
    remove_local_backup_file(tar_file)
    return return_value
  else:
    logging.error("Storage '{0}' not supported.")
    return None

def shutdown_datastore(self):
  """ Top level function for bringing down Cassandra.

  Returns:
    True on success, False otherwise.
  """
  self.__cassandra_backup_lock.acquire(True)
  success = shut_down_cassandra.run()
  self.__cassandra_backup_lock.release()
  if not success:
    return False
  return True

def refresh_data():
  """ Performs a refresh of the data in Cassandra. """
  for column_family in dbconstants.INITIAL_TABLES:
    try:
      call([NODE_TOOL, 'refresh', KEYSPACE, column_family])
    except CalledProcessError as error:
      logging.error('Error while refreshing Cassandra data. Error: {0}'.\
        format(error))

def remove_old_data():
  """ Removes previous node data from the cassandra deployment. """
  for directory in CASSANDRA_DATA_SUBDIRS:
    data_dir = "{0}{1}/{2}".format(constants.APPSCALE_DATA_DIR, "cassandra",
      directory)
    logging.warn("Removing data from {0}".format(data_dir))
    try:
      subprocess.Popen('find /opt/appscale/cassandra -name "*" | '
        'grep ".db\|.txt\|.log" | grep -v snapshot | xargs rm', shell=True)
      logging.info("Done removing data!")
    except CalledProcessError as error:
      logging.error("Error while removing old data from db. Overwriting... "
        "Error: {0}".format(str(error)))

def untar_backup_files():
  """ Restores a previous backup into the Cassandra directory structure
  from a tar ball. 

  Raises:
    BRException: On untar issues.
  """
  logging.info("Untarring Cassandra backup files...")
  try:
    tar = tarfile.open(BACKUP_FILE_LOCATION, "r:gz")
    tar.extractall(path="/")
    tar.close()
  except tarfile.TarError, tar_error:
    logging.exception(tar_error)
    raise backup_exceptions.BRException(
      "Exception while untarring Cassandra backup files.")
  logging.info("Done untarring Cassandra backup files.")

def restore_snapshots():
  """ Restore snapshot into correct directories.

  Returns:
    True on success, False otherwise.
  """
  logging.info("Restoring Cassandra snapshots.")

  for directory in CASSANDRA_DATA_SUBDIRS:
    data_dir = "{0}{1}/{2}/".format(constants.APPSCALE_DATA_DIR, "cassandra",
      directory)
    logging.debug("Restoring in dir {0}".format(data_dir))
    for path, _, filenames in os.walk(data_dir):
      for filename in filenames:
        logging.debug("Restoring: {0}".format(filename))
        if not filename:
          logging.warn("skipping...")
          continue
        full_path = "{0}/{1}".format(path, filename)
        new_full_path = "{0}/../../{1}".format(path, filename)
        logging.debug("{0} -> {1}".format(full_path, new_full_path))
        # Move the files up into the data directory.
        try:
          call(['cp', full_path, new_full_path])
        except CalledProcessError as error:
          logging.error("Error while moving Cassandra snapshot in place. "
            "Error: {0}".format(str(error)))
          return False
  logging.info("Done restoring Cassandra snapshots.")
  return True

def remove_local_backup_file(local_file=BACKUP_FILE_LOCATION):
  """ Removes the local backup file.

  Args:
    local_file: A str, the path to the backup file to delete.
  """
  try:
    call(['rm', '-rf', local_file])
  except CalledProcessError as error:
    logging.error("Error while removing local backup file '{0}'. Error: {1}".\
      format(local_file, str(error)))

def restore_data(storage, path=''):
  """ Restores the Cassandra snapshot. 

  Args:
    storage: A str, one of the StorageTypes class members.
    path: A str, the name of the backup file to restore from.
  """
  if storage == StorageTypes.GCS:
    # Download backup file and store locally with a fixed name.
    if not gcs_helper.download_from_bucket(path, BACKUP_FILE_LOCATION):
      logging.error("Download from GCS failed. Aborting recovery...")
      return False

  create_snapshot()
  if not shut_down_cassandra.run():
    logging.error("Unable to shut down Cassandra. Aborting restore...")
    clear_old_snapshots()
    return False

  remove_old_data()
  try:
    untar_backup_files()
  except backup_exceptions.BRException as br_exception:
    logging.exception("Exception while restoring db snapshots. Need to "
      "rollback... Exception: {0}".format(str(br_exception)))
  restore_snapshots()

  # Start Cassandra and repair.
  start_cassandra.run()
  refresh_data()

  # Local cleanup.
  clear_old_snapshots()
  if storage == StorageTypes.GCS:
    remove_local_backup_file()

  logging.info("Done with restore.")
  return True

if "__main__" == __name__:
  logging.getLogger().setLevel(logging.DEBUG)
  # backup_data(storage='', path='')
  restore_data(storage='', path='')
