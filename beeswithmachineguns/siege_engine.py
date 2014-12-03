import os
import re
import socket
import sys
import time
import urllib2

import boto.ec2
import paramiko

import bees

def _summarize_results(results, params, csv_filename):
    summarized_results = dict()
    summarized_results['timeout_bees'] = [r for r in results if r is None]
    summarized_results['exception_bees'] = [r for r in results if type(r) == socket.error]
    summarized_results['complete_bees'] = [r for r in results if r is not None and type(r) != socket.error]
    summarized_results['timeout_bees_params'] = [p for r, p in zip(results, params) if r is None]
    summarized_results['exception_bees_params'] = [p for r, p in zip(results, params) if type(r) == socket.error]
    summarized_results['complete_bees_params'] = [p for r, p in zip(results, params) if r is not None and type(r) != socket.error]
    summarized_results['num_timeout_bees'] = len(summarized_results['timeout_bees'])
    summarized_results['num_exception_bees'] = len(summarized_results['exception_bees'])
    summarized_results['num_complete_bees'] = len(summarized_results['complete_bees'])

    complete_results = [r['complete_requests'] for r in summarized_results['complete_bees']]
    summarized_results['total_complete_requests'] = sum(complete_results)

    complete_results = [r['failed_requests'] for r in summarized_results['complete_bees']]
    summarized_results['total_failed_requests'] = sum(complete_results)

    complete_results = [r['requests_per_second'] for r in summarized_results['complete_bees']]
    summarized_results['mean_requests'] = sum(complete_results)

    complete_results = [r['ms_per_request'] for r in summarized_results['complete_bees']]
    summarized_results['mean_response'] = sum(complete_results) / summarized_results['num_complete_bees']

    summarized_results['tpr_bounds'] = params[0]['tpr']
    summarized_results['rps_bounds'] = params[0]['rps']

    if summarized_results['tpr_bounds'] is not None:
        if summarized_results['mean_response'] < summarized_results['tpr_bounds']:
            summarized_results['performance_accepted'] = True
        else:
            summarized_results['performance_accepted'] = False

    if summarized_results['rps_bounds'] is not None:
        if summarized_results['mean_requests'] > summarized_results['rps_bounds'] and summarized_results['performance_accepted'] is True or None:
            summarized_results['performance_accepted'] = True
        else:
            summarized_results['performance_accepted'] = False

    summarized_results['request_time_cdf'] = _get_request_time_cdf(summarized_results['total_complete_requests'], summarized_results['complete_bees'])
    if csv_filename:
        _create_request_time_cdf_csv(results, summarized_results['complete_bees_params'], summarized_results['request_time_cdf'], csv_filename)

    return summarized_results

def _attack(params):
    print '(siege) Bee %i is joining the swarm.' % params['i']
    
    """
    Test the target URL with requests.

    Intended for use with multiprocessing.
    """
    print 'Bee %i is joining the swarm.' % params['i']

    try:
        siege_rc = ''
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        pem_path = params.get('key_name') and bees._get_pem_path(params['key_name']) or None
        if not os.path.isfile(pem_path):
            client.load_system_host_keys()
            client.connect(params['instance_name'], username=params['username'])
        else:
            client.connect(
                params['instance_name'],
                username=params['username'],
                key_filename=pem_path)

        print 'Bee %i is firing her machine gun. Bang bang!' % params['i']

        options = ''
        if params['headers'] is not '':
            for h in params['headers'].split(';'):
                if h != '':
                    options += ' -H "%s"' % h.strip()

        if params['cookies'] is not '':
            options += ' -H \"Cookie: %ssessionid=NotARealSessionID;\"' % params['cookies']

        if params['basic_auth'] is not '':
            siege_rc += "login = %(basic_auth)s" % params

        if params['keep_alive']:
            siege_rc += "connection = keep-alive"
        
        if params['post_file']:
            with open (params['post_file'], "r") as myfile:
                data = myfile.read().replace('\n', '')
                params['post_string'] = " POST %s" % data
        else:
            params['post_string'] = ''

        """
        stdin, stdout, stderr = client.exec_command('mktemp')
        params['csv_filename'] = stdout.read().strip()
        if params['csv_filename']:
            options += ' -e %(csv_filename)s' % params
        else:
            print 'Bee %i lost sight of the target (connection timed out creating csv_filename).' % params['i']
            return None
        """

        params['options'] = options

        benchmark_command = 'siege %(options)s -r %(num_requests)s -c %(concurrent_requests)s -b --log=/dev/null "%(url)s%(post_string)s" 2>&1' % params
        if siege_rc is not '':
            client.exec_command("rm ~/.siegerc")
            client.exec_command("echo '%s' > ~/.siegrc" % siege_rc)
        
        stdin, stdout, stderr = client.exec_command(benchmark_command)

        response = {}
        siege_results = stdout.read()
        
        ms_per_request_search = re.search('Response\ time:\s+([^ ]+)',siege_results)

        if not ms_per_request_search:
            print 'Bee %i lost sight of the target (connection timed out).' % params['i']
            return None

        requests_per_second_search = re.search('Transaction\ rate:\s+([^ ]+)', siege_results)
        longest_transaction_search = re.search('Longest\ transaction:\s+(\d+\.\d+)',siege_results)
        shortest_transaction_search = re.search('Shortest\ transaction:\s+(\d+\.\d+)',siege_results)
        achieved_concurrency_search = re.search('Concurrency:\s+(\d+\.\d+)',siege_results)
        complete_requests_search = re.search('Successful\ transactions:\s+(\d+)',siege_results)
        failed_requests_search = re.search('Failed\ transactions:\s+(\d+)',siege_results)

        response['ms_per_request'] = float(ms_per_request_search.group(1))
        response['requests_per_second'] = float(requests_per_second_search.group(1))
        response['longest_txn'] = float(longest_transaction_search.group(1))
        response['shortest_txn'] = float(shortest_transaction_search.group(1))
        response['achieved_concurrency'] = float(achieved_concurrency_search.group(1))
        response['complete_requests'] = int(complete_requests_search.group(1))
        response['total_failed_requests'] = int(failed_requests_search.group(1))

        print 'Bee %i is out of ammo.' % params['i']

        client.close()
        
        return response
    except socket.error, e:
        return e

def median(x):
    if len(x)%2 != 0:
        return sorted(x)[len(x)/2]
    else:
        midavg = (sorted(x)[len(x)/2] + sorted(x)[len(x)/2-1])/2.0
        return midavg

def _print_results(results):
    """
    Print summarized load-testing results.
    """
    timeout_bees = [r for r in results if r is None]
    exception_bees = [r for r in results if type(r) == socket.error]
    complete_bees = [r for r in results if r is not None and type(r) != socket.error]

    num_timeout_bees = len(timeout_bees)
    num_exception_bees = len(exception_bees)
    num_complete_bees = len(complete_bees)

    if exception_bees:
        print '     %i of your bees didn\'t make it to the action. They might be taking a little longer than normal to find their machine guns, or may have been terminated without using "bees down".' % num_exception_bees

    if timeout_bees:
        print '     Target timed out without fully responding to %i bees.' % num_timeout_bees

    if num_complete_bees == 0:
        print '     No bees completed the mission. Apparently your bees are peace-loving hippies.'
        return

    complete_results = [r['complete_requests'] for r in complete_bees]
    total_complete_requests = sum(complete_results)
    print '     Complete requests:\t\t%i' % total_complete_requests

    complete_results = [r['total_failed_requests'] for r in complete_bees]
    total_complete_requests = sum(complete_results)
    print '     Failed requests:\t\t%i' % total_complete_requests

    complete_results = [r['requests_per_second'] for r in complete_bees]
    mean_requests = sum(complete_results) / num_complete_bees
    print '     Requests per second:\t%f [#/sec] (mean)' % mean_requests
    print '     Requests per second:\t%f [#/sec] (med)' % median(complete_results)

    complete_results = [r['ms_per_request'] for r in complete_bees]
    
    if sum([r['complete_requests'] for r in complete_bees]) == 0:
        mean_response = -1
    else:
        mean_response = sum(complete_results) / num_complete_bees
    print '     Time per request:\t\t%f [ms] (mean)' % mean_response
    print '     Time per request:\t\t%f [ms] (med)' % median(complete_results)

    complete_results = [r['longest_txn'] for r in complete_bees]
    max_txn = max(complete_results)
    print '     Longest request:\t\t%f [ms] (max)' % max_txn
    
    complete_results = [r['shortest_txn'] for r in complete_bees]
    min_txn = min(complete_results)
    print '     Shortest request:\t\t%f [ms] (min)' % min_txn
    
    complete_results = [r['achieved_concurrency'] for r in complete_bees]
    mean_conc = sum(complete_results) / num_complete_bees
    print '     Concurrency:\t\t%f (mean)' % mean_conc

    med_conc = median(complete_results)
    print '     Concurrency:\t\t%f (med)' % med_conc

    if mean_response < 0:
        print 'Mission Assessment: Failed miserably, no successes.'
    elif mean_response < 500:
        print 'Mission Assessment: Target crushed bee offensive.'
    elif mean_response < 1000:
        print 'Mission Assessment: Target successfully fended off the swarm.'
    elif mean_response < 1500:
        print 'Mission Assessment: Target wounded, but operational.'
    elif mean_response < 2000:
        print 'Mission Assessment: Target severely compromised.'
    else:
        print 'Mission Assessment: Swarm annihilated target.'