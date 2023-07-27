#!/usr/bin/python3
# vim: set fileencoding=utf-8 :
# Managed by puppet
# Dedicated to Miloš Lokajíček jr., PhD, CSc.
'''
Checks the megariad (AVAGO, LSI, Broadcom) controlles for set of known errors and reports it
Designed for Linux
'''
from argparse import ArgumentParser, ArgumentError
from os import X_OK, access, geteuid, path
from re import IGNORECASE, match, search
from re import split as rsplit
from subprocess import PIPE, Popen
from sys import exit  # pylint: disable=redefined-builtin

VERSION: str = '2023-0727-0946'

NAGIOS_OK: int = 0
NAGIOS_WARNING: int = 1
NAGIOS_CRITICAL:int = 2
NAGIOS_UNKNOWN: int = 3
NAGIOS_ORDER: tuple  = (
    NAGIOS_CRITICAL,
    NAGIOS_WARNING,
    NAGIOS_UNKNOWN,
    NAGIOS_OK,
)
EXPECT_BATTERY: bool = True
EXPECT_HOTSPARE: bool = True
IGNORE_UGOOD: bool = False
IGNORE_OTHERS: bool = True
MISSING_OK: bool = False
MISSING_OK_LIST: set = set()
SLOT_START: int = 0
OUTPUT: str = ''
OUTPUT_LONG: str = ''
STORCLI: str = '/opt/MegaRAID/storcli/storcli64'
EXIT_CODE: int = NAGIOS_OK
EXIT_STATE: str = ''
USE_FAHRENHEIT: bool = False
TEMPERATURE_LIMIT_C_WA: int = 60
TEMPERATURE_LIMIT_C_CR: int = 80
TEMPERATURE_LIMIT_F_WA: int = int((TEMPERATURE_LIMIT_C_WA * 9/5) + 32)
TEMPERATURE_LIMIT_F_CR: int = int((TEMPERATURE_LIMIT_C_CR * 9/5) + 32)
PERFORMANCE_DATA: dict = {}
PERFORMANCE_DATA_STR: str = ''
ERROR_LIMIT_W: int = 1
ERROR_LIMIT_C: int = 11


def handle_nagios_codes(nagios_code_original: int, nagios_code_new: int) -> int:
    '''
    Orders nagios style codes by criticality
    OK->UK->WA->CR
    '''
    nagios_out_code: int = 0
    for nagios_code in NAGIOS_ORDER:
        if nagios_code in {nagios_code_new, nagios_code_original}:
            nagios_out_code = nagios_code
            break
    return nagios_out_code

def end(ecode: int, output: str, long_output: str = '') -> None:
    '''
    Handles the script exit
    '''
    prep: tuple = ('OK', 'WA', 'CR', 'UK')
    separator: str = '' if long_output == '' else '\n'
    print(f'{prep[ecode]}: {output}{separator}{long_output}')
    exit(ecode)

def check_root() -> None:
    '''
    Check if the script is running as a root
    '''
    if geteuid() != 0:
        end(NAGIOS_UNKNOWN, 'Must be run as a root')

def get_output(cmd: list) -> tuple:
    '''
    Calls Popen with PIPE parametres
    '''
    stdout: bytes
    stderr: bytes
    with Popen(cmd, stdout = PIPE, stderr = PIPE) as get_data:
        stdout, stderr = get_data.communicate()
    return (stdout.decode('utf-8'), stderr.decode('utf-8'))

def check_storcli(storcli: str = STORCLI) -> None:
    '''
    Check if the storcli exists and is runnable
    '''
    if not path.exists(storcli):
        end(NAGIOS_UNKNOWN,
            'Could not find the storcli executable',
            f'Expected storcli path is: {storcli}')
    if not access(storcli, X_OK):
        end(NAGIOS_UNKNOWN,
            'The storcli executable is not executable',
            f'Detected storcli path is: {storcli}')
    storcli_version: tuple = get_output([storcli, '-v'])
    if storcli_version[1] != '':
        end(NAGIOS_UNKNOWN,
            'The storcli version query returned an error',
            f'Detected storcli path is: {storcli}')
    if storcli_version[0] == '':
        end(NAGIOS_UNKNOWN,
            'The storcli version query returned empty data',
            f'Detected storcli path is: {storcli}')

def find_relevant_lines(string_to_search: str, header: str) -> list:
    '''
    Find all relevant lines between "----" after defined header
    '''
    relevant_lines: list = []
    start_match: bool = False
    start_parse: bool = False
    for line in string_to_search.split('\n'):
        if not start_match and match(header, line, flags=IGNORECASE):
            start_match = True
        elif start_match:
            if match('-----+$', line):
                if not start_parse:
                    start_parse = True
                else:
                    break
            else:
                relevant_lines.append(line)
    return relevant_lines

def get_controllers() -> list:
    '''
    Fetches all available storcli controllers
    '''
    controller_count: int = 0
    for line in get_output([STORCLI, 'show', 'ctrlcount'])[0].split('\n'):
        if match(r'Controller\s+Count\s+=\s\d+$', line, flags=IGNORECASE):
            controller_count = int(line.split('=')[-1].strip())
            break
    all_controllers: list = []
    for contriller_line in find_relevant_lines(
        get_output([STORCLI, 'show'])[0], r'Ctl\s+Model\s+Ports\s+.*Hlth\s*$'):
        all_controllers.append(rsplit(r'\s+', contriller_line.strip())[0])
    if len(all_controllers) != controller_count:
        end(NAGIOS_UNKNOWN,
            'Detected controller count does not correspond with the reported count.')
    return all_controllers

def get_enclosures(enc_controller: str) -> tuple:
    '''
    Get list of enclosures on controller
    '''
    ge_enclosures: list = []
    ge_enclosures_output: str = ''
    ge_enclosures_output_long: str = ''
    ge_enclosures_ecode: int = NAGIOS_OK
    ge_lines: list = find_relevant_lines(
        get_output([STORCLI, f'/c{enc_controller}/eall', 'show'])[0],
        r'EID\s+State\s+Slots\s+PD.*ProdID\s+VendorSpecific\s*$')

    for enclosure_line in ge_lines:
        enclosure_list: list = rsplit(r'\s+', enclosure_line.strip())
        if 'OK' != enclosure_list[1]:
            ge_enclosures_ecode = handle_nagios_codes(ge_enclosures_ecode, NAGIOS_CRITICAL)
            ge_enclosures_output_long += \
            f'CR: Enclosure /c{enc_controller}/e{enclosure_list[0]}\n'
            ge_enclosures_output += f'/c{enc_controller}/e{enclosure_list[0]};'
        else:
            ge_enclosures_output_long += \
                f'OK: Enclosure /c{enc_controller}/e{enclosure_list[0]}\n'
        ge_enclosures.append(
            (enclosure_list[0], int(enclosure_list[2]), int(enclosure_list[3])))

    return (ge_enclosures_ecode, ge_enclosures, \
        ge_enclosures_output, ge_enclosures_output_long)

def check_cvs(cv_controllers: list) -> tuple:
    '''
    Checks the cache vault backup units
    '''
    cv_ecode: int = NAGIOS_OK
    cv_out: str = ''
    cv_out_long: str = ''
    # do not fail if the CV is missing and EXPECT_BATTERY is False
    for cv_controller in cv_controllers:
        cv_info: str = get_output([STORCLI, f'/c{cv_controller}/cv', 'show'])[0]
        if search(r'\s+Optimal\s+', cv_info, IGNORECASE):
            cv_out_long += f'OK: Battery on /c{cv_controller}\n'
        elif search(r'Cachevault\s+is\s+absent!', cv_info, IGNORECASE):
            if EXPECT_BATTERY:
                cv_ecode = handle_nagios_codes(cv_ecode, NAGIOS_CRITICAL)
                cv_out_long += f'CR: Battery on controller /c{cv_controller} is missing.\n'
                cv_out += f'/c{cv_controller};'
            else:
                cv_out_long += f'OK: Battery on controller /c{cv_controller} is missing, ' \
                     'but this is expected.\n'
        else:
            cv_ecode = handle_nagios_codes(cv_ecode, NAGIOS_CRITICAL)
            cv_out_long += f'CR: Battery on controller /c{cv_controller}\n'
            cv_out += f'/c{cv_controller};'
    return cv_ecode, cv_out, cv_out_long

def check_bbus(bbu_controllers: list) -> tuple:
    '''
    Check the BBU (battery) status
    '''
    cvs_controllers: list = []
    battery_ok: int = NAGIOS_OK
    bbu_out: str = ''
    bbu_out_long: str = ''
    cvs_ecode: int = NAGIOS_OK
    cvs_out: str = ''
    cvs_out_long: str = ''
    # do not fail if the BBU is missing and EXPECT_BATTERY is False
    for bbu_controller in bbu_controllers:
        bbu: str = get_output([STORCLI, f'/c{bbu_controller}/bbu', 'show'])[0]
        if search(r'Failed\s+-\s+use\s+/cx/cv\s+255', bbu, flags=IGNORECASE):
            cvs_controllers.append(bbu_controller)
        elif search(r'\s+Optimal\s+', bbu, flags=IGNORECASE):
            bbu_out_long += f'OK: Battery on /c{bbu_controller}.\n'
        elif search(r'Battery\s+is\s+absent!', bbu, flags=IGNORECASE):
            if EXPECT_BATTERY:
                battery_ok = handle_nagios_codes(battery_ok, NAGIOS_CRITICAL)
                bbu_out_long += f'CR: Battery on controller /c{bbu_controller} is missing.\n'
                bbu_out += f'/c{bbu_controller};'
            else:
                bbu_out_long += f'OK: Battery on controller /c{bbu_controller} is missing, ' \
                     'but this is expected.\n'
        else:
            battery_ok = handle_nagios_codes(battery_ok, NAGIOS_CRITICAL)
            bbu_out_long += f'OK: Battery on /c{bbu_controller}\n'
            bbu_out += f'/c{bbu_controller};'
    cvs_ecode, cvs_out, cvs_out_long = check_cvs(cvs_controllers)
    battery_ok = handle_nagios_codes(battery_ok, cvs_ecode)
    bbu_out_long += cvs_out_long
    bbu_out = '' if battery_ok == NAGIOS_OK else f'{bbu_out}{cvs_out}'
    return (battery_ok, bbu_out, bbu_out_long)

def get_vds(vd_controller: str) -> tuple:
    '''
    Finds all virtual drives on controller
    '''
    vd_out: str = ''
    vd_out_long: str = ''
    vd_out_wa: str = ''
    vd_exit_code: int = NAGIOS_OK
    vd_lines: list = find_relevant_lines(
        get_output([STORCLI, f'/c{vd_controller}/vall', 'show'])[0],
                   r'\s*DG/VD\s+TYPE\s+.*Size\s+Name\s*$')
    for vd_line in vd_lines:
        vd_ok: bool = True
        vd_line_split: list = rsplit(r'\s+', vd_line.strip())
        vd_vd: str = vd_line_split[0].split('/')[1]
        if not match('on', vd_line_split[7], IGNORECASE):
            vd_exit_code = handle_nagios_codes(vd_exit_code, NAGIOS_WARNING)
            vd_out_wa += f'/c{vd_controller}/v{vd_vd};'
            vd_out_long += f'WA: VD /c{vd_controller}/v{vd_vd} does not have scheduled CC\n'
            vd_ok = False
        if match('pdgd', vd_line_split[2]):
            vd_exit_code = handle_nagios_codes(vd_exit_code, NAGIOS_WARNING)
            vd_out_wa += f'/c{vd_controller}/v{vd_vd};'
            vd_out_long += f'WA: VD /c{vd_controller}/v{vd_vd} is partialy degraded\n'
            vd_ok = False
        if match('dgrd', vd_line_split[2]):
            vd_exit_code = handle_nagios_codes(vd_exit_code, NAGIOS_CRITICAL)
            vd_out += f'/c{vd_controller}/v{vd_vd};'
            vd_out_long += f'CR: VD /c{vd_controller}/v{vd_vd} is DEGRADED\n'
            vd_ok = False
        if vd_ok:
            vd_out_long += f'OK: VD /c{vd_controller}/v{vd_vd}\n'
    return (vd_exit_code, vd_out, vd_out_long, vd_out_wa)

def get_drive_state(ds_drive: str) -> list:
    '''
    Queries drive state
    '''
    ds_attributes: dict = {
        r'Manufacturer\s+Id':                               [str,   None],
        r'Model\s+Number':                                  [str,   None],
        r'SN':                                              [str,   None],
        r'Drive\s+Temperature':                             [float, None],
        r'S\.M\.A\.R\.T\s+alert\s+flagged\s+by\s+drive':    [bool,  None],
        r'Media\s+Error\s+Count':                           [int,   None],
        r'Other\s+Error\s+Count':                           [int,   None],
        r'Predictive\s+Failure\s+Count':                    [int,   None],
    }
    def ds_get_value(ds_value_line:str):
        '''
        returns value of the attribute
        '''
        ds_value: str = rsplit(r'\s+=\s+', ds_value_line.strip())[1].strip()
        if match(r'\d+\s*C\s+\(?\d+[.,]\d+\s*F\)?', ds_value, IGNORECASE):
            ds_unit: str = 'F' if USE_FAHRENHEIT else 'C'
            ds_value = search(r'(\d+[,.]?\d*)\s*' + ds_unit, ds_value, IGNORECASE).group(1)
        elif match(r'yes$', ds_value, IGNORECASE):
            ds_value = True
        elif match(r'no$', ds_value, IGNORECASE):
            ds_value = False
        elif match(r'N/A$', ds_value, IGNORECASE):
            ds_value = -99
        return ds_value

    for ds_line in get_output([STORCLI, ds_drive, 'show', 'all'])[0].split('\n'):
        for ds_attribute, ds_data in ds_attributes.items():
            if ds_data[1] is None:
                if match(ds_attribute + r'\s+=\s+', ds_line, IGNORECASE):
                    ds_data[1] = ds_data[0](ds_get_value(ds_line))
                    break
        if all(x[1] is not None for _, x in ds_attributes.items()):
            break
    return (x[1] for _, x in ds_attributes.items())

def detect_empty_slots(empty) -> list:
    '''
    Detects non sequential numbers
    '''
    full: set = set(range(SLOT_START, 2049)) - empty
    non_continuous: set = set()
    if full != set():
        last: int = max(full)
        non_continuous = list(set(range(SLOT_START, last+1)) - full)
        prev_matched: bool = False
        blocks: list = []
        block: list = []
        for i, non_cont_slot in enumerate(non_continuous):
            if i+1 < len(non_continuous) and non_continuous[i+1] == non_cont_slot+1:
                prev_matched = True
                block.append(non_cont_slot)
            elif prev_matched and non_continuous[i-1] == non_cont_slot-1:
                prev_matched = False
                block.append(non_cont_slot)
                blocks.append(block)
                block = []
        for block in blocks:
            if len(block) > 2:
                for slot in block:
                    non_continuous.remove(slot)
    return sorted(non_continuous)

def get_drives(pd_controller: str, pd_enclosure: str) -> tuple:
    '''
    Enumerates the drives
    '''
    pd_exit_code: int = NAGIOS_OK
    pd_out: str = ''
    pd_out_wa: str = ''
    pd_count: int = 0
    pd_hotspare: bool = False
    pd_out_long: str = ''
    pd_unit: str = 'F' if USE_FAHRENHEIT else 'C'
    pd_empty_slots: set = set(range(SLOT_START, 2049))
    pd_temp_limit_wa: int = TEMPERATURE_LIMIT_F_WA if USE_FAHRENHEIT else TEMPERATURE_LIMIT_C_WA
    pd_temp_limit_cr: int = TEMPERATURE_LIMIT_F_CR if USE_FAHRENHEIT else TEMPERATURE_LIMIT_C_CR
    pd_lines: list = find_relevant_lines(
        get_output([STORCLI, f'/c{pd_controller}/e{pd_enclosure}/sall', 'show'])[0],
        r'\s*EID:Slt\s+DID\s+State.*\s+Sp\s+Type\s*$'
    )
    pd_count = len(pd_lines)
    for pd_line in pd_lines:
        pd_line_split: list = rsplit(r'\s+', pd_line.strip())
        pd_slot: int = int(pd_line_split[0].split(':')[1])
        pd_empty_slots.remove(pd_slot)
        pd_state: int = NAGIOS_OK
        pd_smart_ok: str = 'OK'
        pd_manufacturer: str
        pd_model: str
        pd_sn: str
        pd_temp: float
        pd_smart_flag: bool
        pd_media_err: int
        pd_other_err: int
        pd_predictive_err: int
        pd_id: str = f'/c{pd_controller}/e{pd_enclosure}/s{pd_slot}'
        pd_manufacturer, pd_model, pd_sn, pd_temp, pd_smart_flag, \
            pd_media_err, pd_other_err, \
                pd_predictive_err = get_drive_state(pd_id)
        if pd_smart_flag:
            pd_smart_ok = 'FAIL'
        pd_line_out: str = f'{pd_id} '
        if not match('ATA$', pd_manufacturer, IGNORECASE):
            pd_line_out += f'{pd_manufacturer}; '
        pd_line_out += f'{pd_model}; SN: {pd_sn}; Temperature: {pd_temp:6.2f}˚{pd_unit}; '
        pd_line_out += f'Errors: SMART: {pd_smart_ok}, Media: {pd_media_err}, '
        pd_line_out += f'Other: {pd_other_err}, Predictive: {pd_predictive_err}\n'
        if pd_temp >= pd_temp_limit_cr or pd_media_err >= ERROR_LIMIT_C \
            or pd_predictive_err >= ERROR_LIMIT_C:
            pd_state = handle_nagios_codes(pd_state, NAGIOS_CRITICAL)
        elif pd_temp >= pd_temp_limit_wa or pd_media_err >= ERROR_LIMIT_W \
            or pd_predictive_err >= ERROR_LIMIT_W or pd_smart_flag:
            pd_state = handle_nagios_codes(pd_state, NAGIOS_WARNING)

        if not IGNORE_OTHERS:
            if pd_other_err >= ERROR_LIMIT_C:
                pd_state = handle_nagios_codes(pd_state, NAGIOS_CRITICAL)
            elif pd_other_err >= ERROR_LIMIT_W:
                pd_state = handle_nagios_codes(pd_state, NAGIOS_WARNING)

        if match(r'GHS$', pd_line_split[2], IGNORECASE):
            pd_hotspare = True
            pd_line_out = f'PD (GHS) {pd_line_out}'
        elif match(r'DHS$', pd_line_split[2], IGNORECASE):
            pd_hotspare = True
            pd_line_out = f'PD (DHS VD{pd_line_split[3]}) {pd_line_out}'
        elif match(r'Onln$', pd_line_split[2], IGNORECASE):
            pd_line_out = f'PD {pd_line_out}'
        elif match(r'JBOD$', pd_line_split[2], IGNORECASE):
            pd_line_out = f'PD (JBOD) {pd_line_out}'
        elif match(r'UGood$', pd_line_split[2], IGNORECASE):
            pd_line_out = f'PD (Unconfigured Good) {pd_line_out}'
            if not IGNORE_UGOOD:
                pd_state = handle_nagios_codes(pd_state, NAGIOS_WARNING)
        elif match(r'UGShld$', pd_line_split[2], IGNORECASE):
            pd_line_out = f'PD (Unconfigured Good Shielded) {pd_line_out}'
            if not IGNORE_UGOOD:
                pd_state = handle_nagios_codes(pd_state, NAGIOS_WARNING)
        elif match(r'Cpybck$', pd_line_split[2], IGNORECASE):
            pd_line_out = f'PD (CopyBack) {pd_line_out}'
            pd_state = handle_nagios_codes(pd_state, NAGIOS_WARNING)
        elif match(r'Rbld$', pd_line_split[2], IGNORECASE):
            pd_line_out = f'PD (Rebuild) {pd_line_out}'
            pd_state = handle_nagios_codes(pd_state, NAGIOS_WARNING)
        else:
            pd_line_out = f'PD ({pd_line_split[2]}) {pd_line_out}'
            pd_state = handle_nagios_codes(pd_state, NAGIOS_CRITICAL)

        if pd_state == NAGIOS_OK:
            pd_out_long += f'OK: {pd_line_out}'
        if pd_state == NAGIOS_WARNING:
            pd_out_long += f'WA: {pd_line_out}'
            pd_out_wa += f'{pd_id};'
        if pd_state == NAGIOS_CRITICAL:
            pd_out_long += f'CR: {pd_line_out}'
            pd_out += f'{pd_id};'
        PERFORMANCE_DATA.setdefault(pd_id, {})
        if USE_FAHRENHEIT:
            PERFORMANCE_DATA[pd_id]['temperature']: str = \
                f'{pd_temp};{TEMPERATURE_LIMIT_F_WA};{TEMPERATURE_LIMIT_F_CR}'
        else :
            PERFORMANCE_DATA[pd_id]['temperature']: str = \
                f'{int(pd_temp)};{TEMPERATURE_LIMIT_C_WA};{TEMPERATURE_LIMIT_C_CR}'
        PERFORMANCE_DATA[pd_id]['errors_other']: str = f'{pd_other_err}'
        if not IGNORE_OTHERS:
            PERFORMANCE_DATA[pd_id]['errors_other'] += f';{ERROR_LIMIT_W};{ERROR_LIMIT_C}'
        PERFORMANCE_DATA[pd_id]['errors_media']: str = \
            f'{pd_media_err};{ERROR_LIMIT_W};{ERROR_LIMIT_C}'
        PERFORMANCE_DATA[pd_id]['errors_predictive']: str = \
            f'{pd_predictive_err};{ERROR_LIMIT_W};{ERROR_LIMIT_C}'
        PERFORMANCE_DATA[pd_id]['smart_ok']: str = f'{int(pd_smart_flag)};1'
        pd_exit_code = handle_nagios_codes(pd_exit_code, pd_state)

    for pd_missing in detect_empty_slots(pd_empty_slots):
        missing_state: str = 'OK'
        if not MISSING_OK:
            pd_out_wa += f'/c{pd_controller}/e{pd_enclosure}/s{pd_missing};'
            pd_exit_code = handle_nagios_codes(pd_exit_code, NAGIOS_WARNING)
            missing_state: str = 'WA'
        pd_out_long += f'{missing_state}: PD (Missing) ' \
            f'/c{pd_controller}/e{pd_enclosure}/s{pd_missing}\n'

    return (pd_exit_code, pd_out, pd_out_long, pd_out_wa, pd_hotspare, pd_count)

def handle_final_state(hf_ecode: int, hf_state: tuple) -> tuple:
    '''
    Prepares final state string and exit code
    '''
    hf_ecode_specific: int = hf_state[0]
    hf_out_tag: str = hf_state[1]
    hf_out: str = hf_state[2]
    hf_out_wa: str = hf_state[3]
    hf_ecode_final: int = handle_nagios_codes(hf_ecode, hf_ecode_specific)
    hf_out_final: str = f'{hf_out_tag}:'
    hf_end_space: str = ' '
    if hf_out != '':
        hf_out_final = f'{hf_out_final} CR-{hf_out}'
    if hf_out_wa != '':
        hf_out_final = f'{hf_out_final} WA-{hf_out_wa}'
    if hf_ecode_specific == NAGIOS_OK:
        hf_out_final = f'{hf_out_final} OK;'
        if hf_out_tag == 'FC':
            hf_out_final = ''
            hf_end_space = ''
    return hf_ecode_final, f'{hf_out_final}{hf_end_space}'


if __name__ == '__main__':
    BBU_EXIT_CODE: int
    BBU_OUT: str
    BBU_OUT_LONG: str
    ALL_ENCLOSURES_OUT: str = ''
    ALL_ENCLOSURES_OUT_LONG: str = ''
    ALL_ENCLOSURES_EXIT_CODE: int = NAGIOS_OK
    ALL_VDS_OUT: str = ''
    ALL_VDS_OUT_WA: str = ''
    ALL_VDS_OUT_LONG: str = ''
    ALL_VDS_EXIT_CODE: int = NAGIOS_OK
    ALL_DISKS_OUT: str = ''
    ALL_DISKS_OUT_LONG: str = ''
    ALL_DISKS_OUT_WA: str = ''
    ALL_DISKS_EXIT_CODE: int = NAGIOS_OK
    ALL_HOTSPARES_OUT: str = ''
    ALL_HOTSPARES_OUT_LONG: str = ''
    ALL_HOTSPARES_EXIT_CODE: int = NAGIOS_OK
    ALL_FOREIGN_EXIT_CODE: int = NAGIOS_OK
    ALL_FOREIGN_OUT: str = ''
    ALL_FOREIGN_OUT_LONG: str = ''
    check_root()
    parser = ArgumentParser()
    parser.add_argument('-s', '--storcli',
                      help=f'Define path of the storcli executable. Default: "{STORCLI}"',
                      type=str, default=STORCLI)
    parser.add_argument('-b', '--nobattery', action='store_false',
                        help='Sets the battery expected flag to false. ' \
                            'Use when you do not have the BBU installed.')
    parser.add_argument('-H', '--nohotspare', action='store_false',
                        help='Sets the hotspare expected flag to false. ' \
                            'Use when you do not have the HotSpare configured.')
    parser.add_argument('-u', '--ugood', action='store_true',
                        help='Ignores Unconfigured Good drives as a warning. ' \
                            'Use when you do not mind having some Ugoods on your config.')
    parser.add_argument('-m', '--missingok', action='store_true',
                        help='Ignore missing drives (empty slots) as WArning. ' \
                            'Missing drives in of 3 or more drives in a row are detected as OK.')
    parser.add_argument('-M', '--missingoklist', type=str,
                        help='Ignore listed missing drives (empty slots) as WArning.' \
                            'Accepts comma separated list of colon separated values of ' \
                            'controller:enclosure:slot. Example:  "0:1:6,1:25:9"')
    parser.add_argument('-o', '--othererrors', action='store_false',
                        help='Detects other drive errors as WArning. Default is ignored')
    parser.add_argument('-f', '--fahrenheit', action='store_true',
                        help='Use Fahrenheit instead of Celsius as a temperature unit.')
    parser.add_argument('-S', '--slotstart', type=int, default=SLOT_START, \
                        help=f'Set starting number for slot checkinig. Default {SLOT_START}.')
    parser.add_argument('-l', '--limits', type=str, \
                        help='Set colon seperated warning and critical temperature limits. ' \
                            f'Default is "{TEMPERATURE_LIMIT_C_WA}:{TEMPERATURE_LIMIT_C_CR}" for ' \
                            f'Celsius and "{TEMPERATURE_LIMIT_F_WA}:{TEMPERATURE_LIMIT_F_CR}" ' \
                            'for Fahrenheit.')
    parser.add_argument('-v', '--version', action='store_true',
                        help=f'Show the script version. Current: {VERSION}')
    try:
        args = parser.parse_args()
    except ArgumentError:
        end(NAGIOS_UNKNOWN, 'Argument error')

    STORCLI = args.storcli
    EXPECT_BATTERY = args.nobattery
    EXPECT_HOTSPARE = args.nohotspare
    IGNORE_UGOOD = args.ugood
    USE_FAHRENHEIT = args.fahrenheit
    IGNORE_OTHERS = args.othererrors
    SLOT_START = args.slotstart
    MISSING_OK = args.missingok

    if args.version is not None and args.version:
        print(f'Version: {VERSION}')
        exit(0)

    if args.limits is not None:
        if not match(r'\d+:\d+$', args.limits):
            end(NAGIOS_UNKNOWN, 'limits argument accepts only this format "\\d+:\\d+"')
        limits: list = args.limits.split(':')
        if USE_FAHRENHEIT:
            TEMPERATURE_LIMIT_F_WA = int(limits[0])
            TEMPERATURE_LIMIT_F_CR = int(limits[1])
        else:
            TEMPERATURE_LIMIT_C_WA = int(limits[0])
            TEMPERATURE_LIMIT_C_CR = int(limits[1])
    if args.missingoklist is not None:
        missing_list = args.missingoklist
        if match(r'\d+:\d+:\d+(,\d+:\d+:\d+)*$', missing_list):
            for drive in missing_list.split(','):
                MISSING_OK_LIST.add(tuple(drive.split(':')))
        else:
            end(NAGIOS_UNKNOWN, 'missingoklist argument accepts only this format ' \
                '"\\d+:\\d+\\d+[,\\d+:\\d+\\d+]"')

    PERFORMANCE_DATA_TEMP_LIMITS: str = f'{TEMPERATURE_LIMIT_C_WA};{TEMPERATURE_LIMIT_C_CR}'
    if USE_FAHRENHEIT:
        PERFORMANCE_DATA_TEMP_LIMITS: str = f'{TEMPERATURE_LIMIT_F_WA};{TEMPERATURE_LIMIT_F_CR}'

    PERFORMANCE_DATA_ERROR_LIMITS: str = f'{ERROR_LIMIT_W};{ERROR_LIMIT_C}'

    check_storcli(STORCLI)
    CONTROLLERS: list = get_controllers()
    BBU_EXIT_CODE, BBU_OUT, BBU_OUT_LONG = check_bbus(CONTROLLERS)
    for controller in CONTROLLERS:
        HAS_HOTSPARE: bool = False
        # VDs
        vds_exit_code: int
        vds_out: str
        vds_out_long: str
        vds_out_wa:str
        vds_exit_code, vds_out, vds_out_long, vds_out_wa = get_vds(controller)
        ALL_VDS_OUT += vds_out
        ALL_VDS_OUT_LONG += vds_out_long
        ALL_VDS_OUT_WA += vds_out_wa
        ALL_VDS_EXIT_CODE = handle_nagios_codes(ALL_VDS_EXIT_CODE, vds_exit_code)
        # eclosure state
        enclosures: list
        enclosures_out: str
        enclosures_out_long: str
        enclosures_ecode: int
        enclosures_ecode, enclosures, enclosures_out, enclosures_out_long = \
            get_enclosures(controller)
        ALL_ENCLOSURES_EXIT_CODE = handle_nagios_codes(ALL_ENCLOSURES_EXIT_CODE, enclosures_ecode)
        ALL_ENCLOSURES_OUT += enclosures_out
        ALL_ENCLOSURES_OUT_LONG += enclosures_out_long
        for enclosure in enclosures:
            hotspare_detected: bool
            drives_out: str
            drives_out_long: str
            drives_exit_code: int
            drivecount: int
            drives_out_wa: str
            drives_exit_code, drives_out, drives_out_long, drives_out_wa, hotspare_detected, \
                drivecount = get_drives(controller, enclosure[0])
            ALL_DISKS_OUT_LONG += drives_out_long
            ALL_DISKS_OUT += drives_out
            ALL_DISKS_OUT_WA += drives_out_wa
            HAS_HOTSPARE = hotspare_detected or HAS_HOTSPARE
            ALL_DISKS_EXIT_CODE = handle_nagios_codes(ALL_DISKS_EXIT_CODE, drives_exit_code)
            if drivecount != int(enclosure[2]):
                ALL_DISKS_OUT_WA += f'/c{controller}/e{enclosure[0]};'
                ALL_DISKS_OUT_LONG += 'WA: Enclosure /c{controller}/e{enclosure[0]} The disk ' \
                    'count found does not correspond with the disk count reported'
                ALL_DISKS_EXIT_CODE = handle_nagios_codes(ALL_DISKS_EXIT_CODE, NAGIOS_WARNING)
        if not HAS_HOTSPARE:
            if EXPECT_HOTSPARE:
                ALL_HOTSPARES_OUT += f'/c{controller};'
                ALL_HOTSPARES_OUT_LONG += f'WA: HS on /c{controller} is missing\n'
                ALL_HOTSPARES_EXIT_CODE = handle_nagios_codes(
                    ALL_HOTSPARES_EXIT_CODE, NAGIOS_WARNING)
            else:
                ALL_HOTSPARES_OUT_LONG += f'OK: HS on /c{controller} is '\
                    'missing, but this is expected\n'
        foreign: str = get_output([STORCLI, f'/c{controller}/fall', 'show'])[0]
        if not search(r'Couldn\'t\s+find\s+any\s+foreign\s+Configuration', foreign, IGNORECASE):
            ALL_FOREIGN_EXIT_CODE = handle_nagios_codes(ALL_FOREIGN_EXIT_CODE, NAGIOS_WARNING)
            ALL_FOREIGN_OUT_LONG += f'WA: Foreign configurtaion detected on /c{controller}\n'
            ALL_FOREIGN_OUT += f'/c{controller};'


    for specific_state in (
        # Class specific exit code, Tag,    CR output,              WA output
        # CR or WA set to '' if not used in specific HW class
        (ALL_FOREIGN_EXIT_CODE,     'FC',   '',                     ALL_FOREIGN_OUT),
        (ALL_DISKS_EXIT_CODE,       'PDs',  ALL_DISKS_OUT,          ALL_DISKS_OUT_WA),
        (ALL_HOTSPARES_EXIT_CODE,   'HS',   '',                     ALL_HOTSPARES_OUT),
        (ALL_VDS_EXIT_CODE,         'VDs',  ALL_VDS_OUT,            ALL_VDS_OUT_WA),
        (ALL_ENCLOSURES_EXIT_CODE,  'Enc',  ALL_ENCLOSURES_OUT,     ''),
        (BBU_EXIT_CODE,             'Batt', BBU_OUT,                ''),
    ):
        specific_out: str = ''
        EXIT_CODE, specific_out = handle_final_state(EXIT_CODE, specific_state)
        EXIT_STATE += specific_out

    for perf_data_key, perf_data_data in PERFORMANCE_DATA.items():
        for perf_data_data_key, perf_data_data_data in perf_data_data.items():
            PERFORMANCE_DATA_STR += f' {perf_data_key}_{perf_data_data_key}={perf_data_data_data}'

    end(EXIT_CODE, \
        f'{EXIT_STATE.strip()}', \
        f'{ALL_FOREIGN_OUT_LONG}{ALL_DISKS_OUT_LONG}{ALL_HOTSPARES_OUT_LONG}' \
            f'{ALL_VDS_OUT_LONG}{ALL_ENCLOSURES_OUT_LONG}{BBU_OUT_LONG}'\
            f'|{PERFORMANCE_DATA_STR}'
    )
