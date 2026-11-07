import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_launcher
from ge_speed import SpeedSession, SpeedControl, validate_factor
from ge_wine_sessions import identity

PROBE = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <time.h>
#include <unistd.h>
static long long ns(clockid_t clock) {
 struct timespec ts; if(clock_gettime(clock,&ts)) return -1;
 return (long long)ts.tv_sec*1000000000LL+ts.tv_nsec;
}
int main(void) {
 char command[20];
 printf("%ld\n",(long)getpid()); fflush(stdout);
 while(fgets(command,sizeof(command),stdin)) {
  if(command[0]=='e') { execl("/proc/self/exe","probe",(char*)0); return 2; }
  long long boot=ns(CLOCK_BOOTTIME), mono=ns(CLOCK_MONOTONIC), raw=ns(CLOCK_MONOTONIC_RAW), wall=ns(CLOCK_REALTIME);
  struct timespec delay={0,150000000}; nanosleep(&delay,0);
  printf("{\"mono\":%lld,\"raw\":%lld,\"wall\":%lld,\"boot\":%lld,\"start\":%lld,\"end\":%lld}\n",
   ns(CLOCK_MONOTONIC)-mono,ns(CLOCK_MONOTONIC_RAW)-raw,ns(CLOCK_REALTIME)-wall,ns(CLOCK_BOOTTIME)-boot,mono,ns(CLOCK_MONOTONIC));
  fflush(stdout);
 }
 return 0;
}
'''


@unittest.skipUnless(shutil.which('gcc'), 'Requires GCC')
class SpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.addClassCleanup(cls.temporary.cleanup)
        (cls.root/'probe.c').write_text(PROBE)
        source = Path(__file__).resolve().parents[1]/'speed/ge_speed.c'
        cls.architectures = []
        for architecture in ('64','32'):
            directory = cls.root / ('lib'+architecture)
            directory.mkdir()
            result = subprocess.run(['gcc','-m'+architecture,'-std=gnu11','-Wall','-Wextra','-Werror','-O2','-shared','-fPIC',
                                     str(source),'-ldl','-pthread','-o',str(directory/'libge_speed.so')],capture_output=True,text=True)
            if result.returncode:
                if architecture == '64':
                    raise RuntimeError(result.stderr)
                continue
            subprocess.run(['gcc','-m'+architecture,'-O2',str(cls.root/'probe.c'),'-o',str(cls.root/('probe'+architecture))], check=True, capture_output=True)
            if architecture == '32':
                subprocess.run(['gcc','-m32','-O2','-D_TIME_BITS=64','-D_FILE_OFFSET_BITS=64',str(cls.root/'probe.c'),'-o',str(cls.root/'probe32time64')], check=True, capture_output=True)
            cls.architectures.append(architecture)

    def spawn(self, architecture):
        env = dict(os.environ)
        session = SpeedSession(env, self.root)
        self.addCleanup(session.close)
        process = subprocess.Popen([str(self.root/('probe'+architecture))], env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def finish():
            if process.poll() is None:
                process.stdin.close(); process.wait(timeout=5)
            process.stdout.close(); process.stderr.close()
        self.addCleanup(finish)
        self.assertEqual(int(process.stdout.readline()), process.pid)
        return process, SpeedControl(process.pid), session

    def measure(self, process, expected):
        process.stdin.write('sample\n'); process.stdin.flush()
        sample = json.loads(process.stdout.readline())
        for clock in ('mono','raw'):
            self.assertAlmostEqual(sample[clock]/sample['boot'], expected, delta=.15*max(1,expected))
        self.assertAlmostEqual(sample['wall']/sample['boot'], 1, delta=.15)
        self.assertGreaterEqual(sample['end'], sample['start'])
        return sample

    def check_scaling(self, architecture):
        process, control, session = self.spawn(architecture)
        previous = self.measure(process, 1)
        for factor in (2, .5, 1):
            control.set(factor)
            sample = self.measure(process, factor)
            self.assertGreaterEqual(sample['start'], previous['end'])
            # Changing factor must preserve the accumulated clock, not multiply uptime.
            self.assertLess(sample['start']-previous['end'], 5_000_000_000)
            state = control.read()
            self.assertFalse(state['pending'])
            self.assertEqual(state['applied'], factor)
            self.assertGreater(state['calls'], 0)
            previous = sample
        commands = [json.loads(line)['factor'] for line in (session.path/'commands.jsonl').read_text().splitlines()]
        self.assertEqual(commands,[2,.5,1])

    def test_64bit_clocks_slow_down_speed_up_and_reset_continuously(self):
        self.check_scaling('64')

    def test_32bit_clocks_slow_down_speed_up_and_reset_continuously(self):
        if '32' not in self.architectures:
            self.skipTest('Requires gcc-multilib / 32-bit libc development files')
        self.check_scaling('32')
        self.check_scaling('32time64')

    def test_processes_are_independent_and_session_close_restores_normal_speed(self):
        one, control, session = self.spawn('64')
        two, other, _ = self.spawn('64')
        control.set(2)
        self.measure(one,2)
        self.measure(two,1)
        self.assertEqual(other.read()['applied'],1)
        archive = self.root/'archive'
        archive.mkdir(exist_ok=True)
        session.close(archive)
        self.measure(one,1)
        self.assertTrue((archive/'speed-commands.jsonl').exists())

    def test_exec_keeps_control_attached_to_the_same_pid(self):
        process, control, _ = self.spawn('64')
        control.set(2)
        self.measure(process, 2)
        process.stdin.write('exec\n'); process.stdin.flush()
        self.assertEqual(int(process.stdout.readline()), process.pid)
        self.measure(process, 2)
        self.assertEqual(control.read()['applied'], 2)

    def test_ge_wine_cli_starts_prepared_process_and_archives_speed_changes(self):
        fixture = test_launcher.LauncherTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.backend.write_text(f'#!{sys.executable}\nimport os\nos.execl({str(self.root / "probe64")!r}, "probe")\n')
        env = dict(os.environ, HOME=str(fixture.home), PATH=str(fixture.root),
                   XDG_STATE_HOME=str(fixture.root/'state'), XDG_CONFIG_HOME=str(fixture.root/'config'),
                   GE_WINE_SPEED_LIBRARY_DIR=str(self.root))
        prefix = fixture.root/'prefix'
        launcher = subprocess.Popen([sys.executable,str(test_launcher.SOURCE),'--speed-control','--prefix',str(prefix),str(fixture.exe)],
                                    env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            pid = int(launcher.stdout.readline())
            control = SpeedControl(pid)
            control.set(2)
            self.measure(launcher,2)
            self.assertEqual(control.read()['applied'],2)
            launcher.stdin.close()
            self.assertEqual(launcher.wait(timeout=10),0,launcher.stderr.read())
            folder, = (prefix/'logs').iterdir()
            commands = [json.loads(line) for line in (folder/'speed-commands.jsonl').read_text().splitlines()]
            self.assertEqual(commands[0]['factor'],2)
            self.assertEqual(commands[0]['pid'],pid)
            events = [json.loads(line)['event'] for line in (folder/'events.jsonl').read_text().splitlines()]
            self.assertIn('speed_control_enabled',events)
        finally:
            if launcher.poll() is None:
                launcher.kill(); launcher.wait()
            launcher.stdout.close(); launcher.stderr.close()

    def test_ge_memory_cli_sets_and_reads_confirmed_speed(self):
        process, control, _ = self.spawn('64')
        source = Path(__file__).resolve().parents[1]/'ge_memory.py'
        result = subprocess.run([sys.executable,str(source),'--pid',str(process.pid),'--speed','.5'], capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['requested'],.5)
        self.measure(process,.5)
        result = subprocess.run([sys.executable,str(source),'--pid',str(process.pid),'--speed-status'], capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        info = json.loads(result.stdout)
        self.assertEqual(info['applied'],.5)
        self.assertFalse(info['pending'])

    @unittest.skipUnless(os.environ.get('DISPLAY'),'Requires X display')
    def test_gui_applies_factor_and_restores_normal_speed_on_close(self):
        import tkinter as tk
        import ge_memory
        process, control, _ = self.spawn('64')
        original = tk.Tk
        failures = []
        def factory():
            root = original()
            def check():
                try:
                    widgets = root.memory_widgets
                    self.assertFalse(widgets['speed_apply'].instate(['disabled']))
                    widgets['speed_factor'].set('2')
                    widgets['speed_apply'].invoke()
                    self.measure(process,2)
                    widgets['speed_reset'].invoke()
                    self.measure(process,1)
                    widgets['speed_factor'].set('.5')
                    widgets['speed_apply'].invoke()
                    self.measure(process,.5)
                except Exception as error:
                    failures.append(str(error))
                finally:
                    for timer in root.tk.call('after','info'):
                        root.after_cancel(timer)
                    root.tk.call(root.protocol('WM_DELETE_WINDOW'))
            root.after(250,check)
            return root
        with patch('tkinter.Tk',factory), patch('tkinter.messagebox.showerror',side_effect=lambda *args:failures.append(str(args))):
            ge_memory.gui(process.pid)
        self.assertEqual(failures,[])
        self.measure(process,1)
        self.assertEqual(control.read()['applied'],1)

    def test_pid_namespace_mapping_and_session_reset(self):
        process, original, session = self.spawn('64')
        namespace_pid = process.pid + 1000000
        namespace_path = session.path/f'{namespace_pid}-{original.stamp}.control'
        with original.path.open('r+b') as output:
            output.seek(8); output.write(struct.pack('<Q',namespace_pid))
        original.path.rename(namespace_path)
        read_text = Path.read_text
        def read_status(path,*args,**kwargs):
            text = read_text(path,*args,**kwargs)
            if str(path) == f'/proc/{process.pid}/status':
                text = '\n'.join(line for line in text.splitlines() if not line.startswith('NSpid:'))
                text += f'\nNSpid:\t{process.pid}\t{namespace_pid}\n'
            return text
        with patch.object(Path,'read_text',read_status):
            control = SpeedControl(process.pid)
            self.assertEqual(control.namespace_pid,namespace_pid)
            control.set(2)
            self.measure(process,2)
            self.assertEqual(control.read()['pid'],process.pid)
            session.close()
            self.measure(process,1)

    def test_factor_validation_and_pid_identity(self):
        for value in ('nan','inf','0','-1','21','not-a-number'):
            with self.assertRaises(ValueError): validate_factor(value)
        self.assertEqual(validate_factor('0,5'),.5)
        process, control, session = self.spawn('64')
        with self.assertRaises(ProcessLookupError):
            SpeedControl(process.pid, session.path, expected_stamp='stale')
        self.assertEqual(control.stamp,identity(process.pid))

    def test_unprepared_process_is_reported_without_injection(self):
        with self.assertRaisesRegex(ValueError,'neu über GE-Wine'):
            SpeedControl(os.getpid())
