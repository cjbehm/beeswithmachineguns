
import os
import re
import socket
import sys
import time
import urllib2

import boto
import paramiko

def _attack(params):
    print 'Bee %i is joining the swarm.' % params['i']
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            params['instance_name'],
            username=params['username'],
            key_filename=params['keypath'])

        print 'Bee %i is firing his machine gun. Bang bang!' % params['i']

        response = {}
        stdin, stdout, stderr = client.exec_command('siege -r %(num_requests)s -c %(concurrent_requests)s -b --log=/dev/null "%(url)s" 2>&1' % params)

        response = {}
        siege_results = stdout.read()
        ms_per_request_search = re.search('Response\ time:\s+([^ ]+)',siege_results)

        if not ms_per_request_search:
            print 'Bee %i lost sight of the target (connection timed out).' % params['i']
            return None
        print siege_results

        requests_per_second_search = re.search('Transaction\ rate:\s+([^ ]+)', siege_results)
        longest_transaction_search = re.search('Longest\ transaction:\s+(\d+\.\d+)',siege_results)
        shortest_transaction_search = re.search('Shortest\ transaction:\s+(\d+\.\d+)',siege_results)
        achieved_concurrency_search = re.search('Concurrency:\s+(\d+\.\d+)',siege_results)
        complete_requests_search = re.search('Successful\ transactions:\s+(\d+)',siege_results)

        response['ms_per_request'] = float(ms_per_request_search.group(1))
        response['requests_per_second'] = float(requests_per_second_search.group(1))
        response['longest_txn'] = float(longest_transaction_search.group(1))
        response['shortest_txn'] = float(shortest_transaction_search.group(1))
        response['achieved_concurrency'] = float(achieved_concurrency_search.group(1))
        response['complete_requests'] = int(complete_requests_search.group(1))

        print 'Bee %i is out of ammo.' % params['i']

        client.close()
        
        return response
    except socket.error, e:
        return e

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

    complete_results = [r['requests_per_second'] for r in complete_bees]
    mean_requests = sum(complete_results)
    print '     Requests per second:\t%f [#/sec] (mean)' % mean_requests

    complete_results = [r['ms_per_request'] for r in complete_bees]
    mean_response = sum(complete_results) / num_complete_bees
    print '     Time per request:\t\t%f [ms] (mean)' % mean_response

    complete_results = [r['longest_txn'] for r in complete_bees]
    max_txn = max(complete_results)
    print '     Longest request:\t\t%f [ms] (max)' % max_txn
    
    complete_results = [r['shortest_txn'] for r in complete_bees]
    min_txn = min(complete_results)
    print '     Shortest request:\t\t%f [ms] (min)' % min_txn
    
    complete_results = [r['achieved_concurrency'] for r in complete_bees]
    mean_conc = sum(complete_results) / num_complete_bees
    print '     Concurrency:\t\t%f [ms] (mean)' % mean_conc


    if mean_response < 500:
        print 'Mission Assessment: Target crushed bee offensive.'
    elif mean_response < 1000:
        print 'Mission Assessment: Target successfully fended off the swarm.'
    elif mean_response < 1500:
        print 'Mission Assessment: Target wounded, but operational.'
    elif mean_response < 2000:
        print 'Mission Assessment: Target severely compromised.'
    else:
        print 'Mission Assessment: Swarm annihilated target.'