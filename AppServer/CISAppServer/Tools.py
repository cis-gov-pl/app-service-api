# -*- coding: UTF-8 -*-
"""
Utility classes used by CISAppServer: validation, communication with queue
managers, etc.
"""

import os
try:
    import json
except:
    import simplejson as json
import string
import stat
import re
import shutil
import logging
#import time

from subprocess import Popen, PIPE, STDOUT

from Config import conf, VERBOSE

logger = logging.getLogger(__name__)


def rmtree_error(function, path, exc_info):
    """Exception handler for shutil.rmtree()."""

    logger.error("@rmtree - Cannot remove job output: %s %s" %
                 (function, path), exc_info=exc_info)


def verbose(msg, exc_info=False):
    """
    Log message with VERBOSE log level.

    VERBOSE log level is higher than DEBUG and should be used for large debug
    messages, e.g. data dumps, output from subprocesses, etc.
    """
    logger.log(VERBOSE, msg, exc_info=exc_info)


class CTemplate(string.Template):
    """
    Class used to substitute placeholder strings in script templates.
    The placeholders have following form: @@{KEY}.
    """
    #: Delimiter identifying keywords.
    delimiter = '@@'
    idpattern = '[_a-z0-9]+'


class Service(dict):
    """
    """
    def __init__(self, data, *args, **kwargs):
        # Service is a dict. Make all the keys accessible as attributes while
        # retaining the dict API
        super(Service, self).__init__(*args, **kwargs)
        self.__dict__ = self

        self.config = data["config"]
        self.variables = data["variables"]
        self.sets = data["sets"]

class Validator(object):
    """
    Class responsible for validation of job input data.
    It is also responsible for preparation of PBS scripts.
    """

    def __init__(self):
        """
        Upon initialisation load services configuration.
        """

        #: Services configurations
        self.services = {}

        # Load all files from service_conf_path. Configuration files should be
        # in JSON format.
        logger.debug('@Validator - Loading services configurations.')
        _path = conf.service_path_conf
        _services = os.listdir(_path)
        for _service in _services:
            #TODO validate options structure
            _file_name = os.path.join(_path, _service)
            # Allow for subdirectories
            if not os.path.isfile(_file_name):
                continue
            with open(_file_name) as _f:
                _data = conf.json_load(_f)
            self.services[_service] = Service(_data)
            logger.info("Initialized service: %s" % _service)
        verbose(json.dumps(self.services))

    def validate(self, job):
        """
        Validate job input data and update :py:class:`Job` instance with
        validated data.

        :param job: :py:class:`Job` instance
        :rerurn: True on success False otherwise.
        """

        # Do not validate jobs for the second time. This will conserve
        # resources in case shceduler queue is full and we try to resubmit
        if job.valid_data:
            return True

        _data = job.data

        # Check if data contains service attribute and that such service was
        # initialized
        if 'service' not in _data.keys() or \
           _data['service'] not in self.services.keys() or \
           _data['service'] == 'default':
            job.die("@Validator - Not supported service: %s." %
                    _data['service'], err=False)
            return False

        # Load defaults
        _variables = {_k: _v['default']
                      for _k, _v in self.services[_data['service']].variables.items()}
        _variables.update({_k: _v['default']
                           for _k, _v in self.services['default'].variables.items()})

        # Load sets
        for _k, _v in _data.items():
            if _k in self.services[_data['service']].sets.keys():
                if isinstance(_v, str) or isinstance(_v, unicode):
                    # Value specified as string - check the format using python
                    # builtin conversion
                    _v = int(_v)
                elif not isinstance(value, int):
                    # Value specified neither as int nor string - raise error
                    job.die(
                        "@Validator - Set variables have to be of type int or "
                        "string. (%s: %s)" % (_k, _v),
                        err=False
                    )
                if _v != 1:
                    job.die(
                        "@Validator - Set variables only accept value of 1. "
                        "(%s: %s)" % (_k, _v),
                        err=False
                    )
                    return False
                _variables.update(
                    {_kk: _vv for _kk, _vv in
                     self.services[
                         _data['service']
                     ].sets[_k]['values'].items()}
                )
                del _data[_k]

        # Load variables
        for _k, _v in _data.items():
            if _k in conf.service_reserved_keys and _k != 'service':
                job.die("@Validator - '%s' variable name is restricted." % _k,
                        err=False)
                return False
            elif _k in self.services[_data['service']].variables.keys() or \
               _k == 'service':
                _variables[_k] = _v
            else:
                job.die("@Validator - Not supported variable: %s." % _k,
                        err=False)
                return False

        # Check that all attribute names are defined in service configuration
        # Validate values of the attributes
        for _k, _v in _variables.items():
            if _k in self.services[_data['service']].variables.keys():
                if not self.validate_value(_k, _v,
                        self.services[_data['service']]):
                    job.die(
                        "@Validator - Variable value not allowed: %s - %s." %
                        (_k, _v), err=False
                    )
                    return False
                else:
                    _variables[_k] = _v
                    logger.debug(
                        "@Validator - Value passed validation: %s - %s" %
                        (_k, _v)
                    )
            # Check for possible reserved attribuet names like service, name,
            # date
            elif _k in conf.service_reserved_keys:
                if not self.validate_value(_k, _v, self.services['default']):
                    job.die(
                        "@Validator - Variable value not allowed: %s - %s." %
                        (_k, _v), err=False
                    )
                    return False
                else:
                    _variables[_k] = _v
                    logger.debug(
                        "@Validator - Value passed validation: %s - %s" %
                        (_k, _v)
                    )
            else:
                job.die("@Validator - Not supported variable: %s." % _k,
                        err=False)
                return False

        # Update job data with default values
        job.valid_data = _variables
        verbose('@Validator - Validated input data:')
        verbose(job.valid_data)

        return True

    def validate_value(self, key, value, service):
        """
        Validate value for specified service attribute.

        :param key: attribute name
        :param value: value to validate
        :param service: service object for which attribute is defined
        :return: True on success False otherwise.
        """

        if service.variables[key]['type'] == 'string':
            # Attribute of type string check the table of allowed values
            if not value in service.variables[key]['values']:
                logger.warning("@Validator - Value not allowed: %s - %s." %
                               (key, value))
                return False
        elif service.variables[key]['type'] == 'int':
            # Attribute of type int - check the format
            try:
                if isinstance(value, str) or isinstance(value, unicode):
                    # Value specified as string - check the format using python
                    # builtin conversion
                    _v = int(value)
                elif not isinstance(value, int):
                    # Value specified neither as int nor string - raise error
                    raise ValueError("%s is not an int" % value)
                else:
                    _v = value
            except ValueError:
                logger.warning('@Validator - Value is not a proper int.',
                               exc_info=True)
                return False

            # Check that atrribute value falls in allowed range
            try:
                if _v < service.variables[key]['values'][0] or \
                        _v > service.variables[key]['values'][1]:
                    logger.warning(
                        "@Validator - Value not in allowed range: %s - %s" %
                        (key, _v)
                    )
                    return False
            except IndexError:
                logger.error(
                    "@Validator - Badly defined range for variable:  %s" %
                    key
                )
                return False
        elif service.variables[key]['type'] == 'float':
            # Attribute of type float - check the format
            try:
                if isinstance(value, str) or isinstance(value, unicode):
                    # Value specified as string - check the format using python
                    # builtin conversion
                    _v = float(value)
                elif not isinstance(value, float):
                    # Value specified neither as float nor string - raise error
                    raise ValueError("%s is not a float" % value)
                else:
                    _v = value
            except ValueError:
                logger.warning('@Validator - Value is not a proper float',
                               exc_info=True)
                return False

            # Check that atrribute value falls in allowed range
            try:
                if _v < service.variables[key]['values'][0] or \
                        _v > service.variables[key]['values'][1]:
                    logger.warning(
                        "@Validator - Value not in allowed range: %s - %s" %
                        (key, _v)
                    )
                    return False
            except IndexError:
                logger.error(
                    "@Validator - Badly defined range for variable:  %s" %
                    key
                )
                return False

        return True


class Scheduler(object):
    """
    Virtual Class implementing simple interface for execution backend. Actual
    execution backends should derive from this class and implement defined
    interface.

    Allows for job submission, deletion and extraction of job status.
    """

    # We do not want to output progress with every status check. To lighten the
    # burden lets do it every n-th step
    __progress_step = 0

    def __init__(self):
        #: Working directory path
        self.work_path = None
        #: Path where submitted job IDs are stored
        self.queue_path = None
        #: Default queue
        self.default_queue = None
        #: Maximum number of concurent jobs
        self.max_jobs = None

    def submit(self, job):
        """
        Submit a job for execution. The "pbs.sh" script should be already
        present in the work_path/job directory.

        :param job: :py:class:`Job` instance
        :return: True on success and False otherwise.
        """
        raise NotImplementedError

    def status(self, job):
        """
        Return status of the job in execution queue.

        :param job: :py:class:`Job` instance

        Returns one of "waiting", "running", "done", "unknown".
        """
        # Output job progress log if it exists
        # The progres log is extraced every n-th status check
        if Scheduler.__progress_step >= conf.config_progress_step:
            logger.debug('@Scheduler - Extracting progress log')
            _work_dir = os.path.join(self.work_path, job.id)
            _output_dir = os.path.join(conf.gate_path_output, job.id)
            _progress_file = os.path.join(_work_dir, 'progress.log')
            if os.path.exists(_progress_file):
                try:
                    if not os.path.isdir(_output_dir):
                        os.mkdir(_output_dir)
                    shutil.copy(_progress_file, _output_dir)
                    logger.debug('@Scheduler - Progress log extracted')
                except:
                    logger.error(
                        '@Scheduler - Cannot copy progress.log',
                        exc_info=True
                    )
            Scheduler.__progress_step = 0
        else:
            Scheduler.__progress_step += 1

    def stop(self, job):
        """Stop running job and remove it from execution queue."""
        raise NotImplementedError

    def finalise(self, job):
        """
        Prepare output of finished job.

        Job working directory is moved to the output_path directory.

        :param job: :py:class:`Job` instance
        :return: True on success and False otherwise.
        """
        raise NotImplementedError

    def generate_scripts(self, job):
        """
        Generate scripts and job input files from templates.

        Will walk through service_data_path/service and copy all files
        including recursion through subdirectories to PBS work directory for
        specified job (the directory structure is retained). For all files
        substitute all occurences of @@{atribute_name} with specified values.

        :param job: :py:class:`Job` instance after validation
        :return: True on success and False otherwise.
        """

        # Input directory
        _script_dir = os.path.join(conf.service_path_data,
                                   job.valid_data['service'])
        # Output directory
        _work_dir = os.path.join(self.work_path, job.id)

        # Verify that input dir exists
        if not os.path.isdir(_script_dir):
            job.die("@Scheduler - Service data directory not found: %s." %
                    _script_dir)
            return False

        # Verify that input dir contains "pbs.sh" script
        if not os.path.isfile(os.path.join(_script_dir, 'pbs.sh')):
            job.die("@Scheduler - Missing \"pbs.sh\" script for service %s." %
                    job.valid_data['service'])
            return False

        # Create output dir
        if not os.path.isdir(_work_dir):
            try:
                os.mkdir(_work_dir)
            except IOError:
                job.die("@Scheduler - Unable to create WORKDIR %s." %
                        _work_dir, exc_info=True)
                return False

        # Recurse through input dir
        logger.debug("@Scheduler - generate scripts")
        for _path, _dirs, _files in os.walk(_script_dir):
            # Relative paths for subdirectories
            _sub_dir = re.sub("^%s" % _script_dir, '', _path)
            logger.debug("@Scheduler - Sub dir: %s" % _sub_dir)
            if _sub_dir:
                # Remove starting /
                _sub_dir = _sub_dir[1:]
                _out_dir = os.path.join(_work_dir, _sub_dir)
            else:
                _out_dir = _work_dir

            # Create subdirectories in output dir
            for _dir in _dirs:
                _name = os.path.join(_out_dir, _dir)
                try:
                    os.mkdir(_name)
                except:
                    job.die(
                        "@Scheduler - Cannot create job subdirectory %s." %
                        _name, exc_info=True
                    )
                    return False

            # Iterate through script files in current subdir
            for _file in _files:
                # Skip editor buffers and recovery files
                if _file.endswith('~'):
                    continue
                if _file.startswith('.') and _file.endswith('.swp'):
                    continue
                # Input and output file names
                _fin_name = os.path.join(_path, _file)
                _fou_name = os.path.join(_out_dir, _file)
                try:
                    # Open input template script and output file
                    _fin = open(_fin_name, 'r')
                    _fou = open(_fou_name, 'w')

                    # Loop through input lines and perform substitutions using
                    # string.Template module.
                    for _line in _fin:
                        # REGEXPS
                        _t = CTemplate(_line)
                        _line = _t.substitute(job.valid_data)
                        _fou.write(_line)
                    # Close files
                    _fin.close()
                    _fou.close()
                    # Copy file permisions
                    _st = os.stat(_fin_name)
                    os.chmod(_fou_name, _st.st_mode)
                except:
                    job.die(
                        "@Scheduler - Scripts creation failed for job: %s." %
                        job.id, exc_info=True
                    )
                    return False

        # Make sure that "pbs.sh" is executable
        try:
            _st = os.stat(os.path.join(_script_dir, 'pbs.sh'))
            os.chmod(os.path.join(_work_dir, 'pbs.sh'),
                     _st.st_mode | stat.S_IXUSR)
        except:
            job.die(
                "@Scheduler - Unable to change permissions for pbs.sh: %s." %
                job.id, exc_info=True
            )
            return False

        # Make sure that "epilogue.sh" is executable
        # Make sure it is not writable by group and others - torque will
        # silently ignore it otherwise
        try:
            _st = os.stat(os.path.join(_script_dir, 'epilogue.sh'))
            os.chmod(os.path.join(_work_dir, 'epilogue.sh'),
                     (_st.st_mode | stat.S_IXUSR) &
                     ( ~stat.S_IWGRP & ~stat.S_IWOTH))
        except:
            job.die(
                "@Scheduler - Unable to change permissions for epilogue.sh: %s." %
                job.id, exc_info=True
            )
            return False

        return True


class PbsScheduler(Scheduler):
    """
    Class implementing simple interface for PBS queue system.

    Allows for job submission, deletion and extraction of job status.
    """

    def __init__(self):
        #: PBS Working directory path
        self.work_path = conf.pbs_path_work
        #: Path where submitted PBS job IDs are stored
        self.queue_path = conf.pbs_path_queue
        #: Default PBS queue
        self.default_queue = conf.pbs_default_queue
        #: Maximum number of concurent jobs
        self.max_jobs = conf.pbs_max_jobs

    def submit(self, job):
        """
        Submit a job to PBS queue. The "pbs.sh" script should be already
        present in the pbs_work_path/job directory.

        :param job: :py:class:`Job` instance
        :return: True on success and False otherwise.
        """

        # Check that maximum job limit is not exceeded
        try:
            _queue = os.listdir(self.queue_path)
        except:
            logger.error("@PBS - unable to read queue directory %s." %
                         self.queue_path, exc_info=True)
            return False
        if len(_queue) >= self.max_jobs:
            return False

        # Path names
        _work_dir = os.path.join(self.work_path, job.id)
        _run_script = os.path.join(_work_dir, "pbs.sh")
        _output_log = os.path.join(_work_dir, "output.log")
        # Select queue
        _queue = self.default_queue
        if job.valid_data['queue'] != "":
            _queue = job.valid_data['queue']

        try:
            # Submit
            logger.debug("@PBS - Submitting new job")
            _opts = ['/usr/bin/qsub', '-q', _queue,
                     '-d', _work_dir, '-j', 'oe', '-o',  _output_log,
                     '-l', 'epilogue=epilogue.sh', _run_script]
            logger.debug("@PBS - Running command: %s" % str(_opts))
            _proc = Popen(_opts, stdout=PIPE, stderr=STDOUT)
            _output = _proc.communicate()
            # Hopefully qsub returned meaningful job ID
            _jid = _output[0]
            # Check return code. If qsub was not killed by signal Popen will
            # not rise an exception
            if _proc.returncode != 0:
                raise OSError((
                    _proc.returncode,
                    "/usr/bin/qsub returned non zero exit code.\n%s" %
                    str(_output)
                ))
        except:
            job.die("@PBS - Unable to submit job %s." % job.id, exc_info=True)
            return False

        # Store the PBS job ID into a file
        with open(os.path.join(self.queue_path, job.id), 'w') as _jid_file:
            _jid_file.write(_jid)
        logger.info("Job successfully submitted: %s" % job.id)
        return True

    def status(self, job):
        """
        Return status of the job in PBS queue.

        :param job: :py:class:`Job` instance

        Returns one of "waiting", "running", "done", "unknown".
        """

        super(PbsScheduler, self).status(job)

        _done = 0
        _status = 'unknown'
        _pbs_id = ''
        _work_dir = os.path.join(self.work_path, job.id)
        try:
            with open(os.path.join(self.queue_path, job.id)) as _pbs_file:
                _pbs_id = _pbs_file.readline().strip()
        except:
            job.die('@PBS - Unable to read PBS job ID', exc_info=True)
            return _status

        try:
            # Run qstat
            logger.debug("@PBS - Check job state")
            _opts = ["/usr/bin/qstat", "-f", _pbs_id]
            _proc = Popen(_opts, stdout=PIPE, stderr=STDOUT)
            _output = _proc.communicate()[0]
            if _proc.returncode == 153:
                logger.debug(
                    '@PBS - Job ID missing from PBS queue: done or error')
                _done = 1
            # Check return code. If qstat was not killed by signal Popen will
            # not rise an exception
            elif _proc.returncode != 0:
                raise OSError((
                    _proc.returncode,
                    "/usr/bin/qstat returned non zero exit code.\n%s" %
                    str(_output)
                ))
        except:
            logger.error("@PBS - Unable to check job %s state." %
                         job.id, exc_info=True)
            return _status

        if _done == 0:
            verbose('@PBS - Qstat returned meaningful output. Start parsing.')
            verbose(str(_output))
            _re = re.compile('^job_state = (.*)')
            for _line in _output.split('\n'):
                _m = _re.match(_line.strip())
                if _m is not None:
                    _res = _m.group(1)
                    logger.debug("@PBS - Found job_state: %s" % _res)
                    try:
                        # Consider running, exiting and complete as running
                        if _res == 'R' or _res == 'C' or _res == 'E':
                            _status = 'running'
                            job.set_state('running')
                        # Other states are considered as queued
                        else:
                            _status = 'queued'
                            job.set_state('queued')
                    except:
                        job.die("@PBS - Unable to set state of job %s" %
                                job.id, exc_info=True)
                        return 'unknown'

        # When job is finished either epilogue was executed and status.dat is
        # present. Otherwise assume it was killed
        elif os.path.isfile(os.path.join(_work_dir, 'status.dat')):
            logger.debug("@PBS - Found job state: D")
            _status = 'done'
        else:
            _status = 'killed'

        return _status

    def stop(self, job, msg):
        """
        Stop running job and remove it from PBS queue.

        :param job: :py:class:`Job` instance
        :param msg: Message that will be passed to the user
        :return: True on success and False otherwise.
        """
        _status = True  # Return value
        _pbs_id = ''
        _work_dir = os.path.join(self.work_path, job.id)

        # Get Job PBS ID
        try:
            with open(os.path.join(self.queue_path, job.id)) as _pbs_file:
                _pbs_id = _pbs_file.readline().strip()
        except:
            job.die('@PBS - Unable to read PBS job ID', exc_info=True)
            _status = False

        # Run qdel
        try:
            logger.debug("@PBS - Killing job")
            _opts = ["/usr/bin/qdel", _pbs_id]
            _proc = Popen(_opts, stdout=PIPE, stderr=STDOUT)
            _output = _proc.communicate()[0]
            # Check return code. If qstat was not killed by signal Popen will
            # not rise an exception
            if _proc.returncode != 0:
                raise OSError((
                    _proc.returncode,
                    "/usr/bin/qdel returned non zero exit code.\n%s" %
                    str(_output)
                ))
        except:
            job.die("@PBS - Unable to terminate job %s." %
                    job.id, exc_info=True)
            _status = False

        # Remove PBS ID file
        try:
            os.unlink(os.path.join(self.queue_path, job.id))
        except:
            job.die("@PBS - Unable to remove job id file %s" %
                    job.id, exc_info=True)
            _status = False

        # Remove the working directory and its contents
        if os.path.isdir(_work_dir):
            shutil.rmtree(_work_dir, onerror=rmtree_error)

        # Set job state as killed
        if _status:
            job.exit(msg, state='killed')

        return _status

    def finalise(self, job):
        """
        Prepare output of finished job.

        Job working directory is moved to the external_data_path directory.

        :param job: :py:class:`Job` instance
        :return: True on success and False otherwise.
        """

        logger.debug("@PBS - Retrive job output: %s" % job.id)
        _work_dir = os.path.join(self.work_path, job.id)
        _job_state = 'abort' # Job state
        _status = 0 # Job exit status

        # Get job output code
        try:
            with open(os.path.join(_work_dir, 'status.dat')) as _status_file:
                _status = int(_status_file.readline().strip())
                _job_state = 'done'
        except:
            _job_state = 'killed'
            logger.warning("@PBS - Unable to extract job exit code: %s. "
                           "Will continue with output extraction" % job.id,
                           exc_info=True)
            # Although there is no output code job might finished only epilogue
            # failed. Let the extraction finish.

        try:
            os.unlink(os.path.join(self.queue_path, job.id))
            # Remove output dir if it exists.
            _out_dir = os.path.join(conf.gate_path_output, job.id)
            _dump_dir = os.path.join(conf.gate_path_dump, job.id)
            if os.path.isdir(_out_dir):
                logger.debug('@PBS - Remove existing output directory')
                # out and dump should be on the same partition so that rename
                # is used. This will make sure that processes reading from out
                # will not cause rmtree to throw exceptions
                shutil.move(_out_dir, _dump_dir) 
                shutil.rmtree(_dump_dir, ignore_errors=True)
            shutil.move(_work_dir, conf.gate_path_output)
            logger.info("Job %s output retrived." % job.id)
        except:
            _job_state = 'abort'
            logger.error("@PBS - Unable to retrive job output directory %s" %
                         _work_dir, exc_info=True)

        if _job_state == 'killed':
            job.exit("", state='killed')
        elif _job_state == 'done':
            if _status == 0:
                job.exit("%d" % _status)
            else:
                job.exit("%d" % _status, state='failed')
        else:
            job.die("@PBS - Unable to finalise job %s." % job.id)
            return False

        return True
