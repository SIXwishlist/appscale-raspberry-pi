#!/usr/bin/ruby -w


$:.unshift File.join(File.dirname(__FILE__))
require 'helperfunctions'
require 'monit_interface'


# Starts and stops the datastore groomer service.
module GroomerService

  # This variable is the maximum memory allowed for the groomer process.
  MAX_MEM = 512

  # Starts the Groomer Service on this machine. We don't want to monitor
  # it ourselves, so just tell monit to start it and watch it.
  def self.start()
    groomer = self.scriptname()
    start_cmd = "/usr/bin/python2 #{groomer}"
    stop_cmd = "/usr/bin/python2 #{APPSCALE_HOME}/scripts/stop_service.py " +
      "#{groomer} /usr/bin/python2"
    MonitInterface.start(:groomer_service, start_cmd, stop_cmd, "9999", {},
      nil, nil, start_cmd, MAX_MEM)
    MonitInterface.start_file(:groomer_file_check,
      "/var/log/appscale/groomer_service-9999.log", stop_cmd, "12")
  end

  # Stops the groomer service running on this machine. Since it's
  # managed by monit, just tell monit to shut it down.
  def self.stop()
    MonitInterface.stop(:groomer_service)
    MonitInterface.stop(:groomer_file_check)
  end

  def self.scriptname()
    return "#{APPSCALE_HOME}/AppDB/groomer_service.py"
  end

end

