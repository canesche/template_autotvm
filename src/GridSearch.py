""" Grid algorithm """

import sys, os

# from tvm.auto_scheduler.space import Space
from tvm.auto_scheduler.search_task import SearchTask
from tvm.autotvm.tuner.index_based_tuner import GridSearchTuner

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(SCRIPT_DIR))

import os
from src.utils import *
from src.space import Space


class GridSearch:
    def __init__(self, json_file, target, log):

        workload_key = json_file["i"][0][0]
        self.task = SearchTask(workload_key=workload_key, target=target)
        self.space = Space(json_file, self.task, False)
        self.final_log = log
        self.count, self.index = 1, 0
        self.visited, self.batch = set([0]), max(os.cpu_count(), 16)
        self.dims = self.space.dims

        # if doesn't have opt just copy the solution from Ansor template
        if self.space.total_dims == 1:
            self.final_log = write_file([json_file], log)

        self.begin_idx, self.end_idx = 0, self.space.total_dims
        self.range_length = self.end_idx - self.begin_idx
        self.visited_max = self.range_length

    def has_next(self):
        return len(self.visited) < self.visited_max and self.count <= self.trials

    def next_batch(self, batch_size):
        i, json_file_list = 0, []
        while i < batch_size and self.has_next():
            if self.index >= self.begin_idx and self.index < self.end_idx:
                self.visited.add(self.index)
                json_file_list.append(
                    self.space.apply_opt(self.space.point2knob(self.index))
                )
            self.index += 1
            i, self.count = i + 1, self.count + 1
        log = write_file(json_file_list)
        return self.space.run(log, self.final_log)

    def tune(
        self,
        n_trial=100,
        measure_option=None,
        early_stopping=None,
        callbacks=(),
        si_prefix="G",
    ):
        self.trials = n_trial
        while self.has_next():
            _, _ = self.next_batch(self.batch)
