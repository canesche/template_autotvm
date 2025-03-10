""" DPMeta algorithm """

import sys, os, logging
from tvm.auto_scheduler.search_task import SearchTask

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(SCRIPT_DIR))

import os
from src.utils import *
from src.space_ms import Space

LOGGER = logging.getLogger("autotvm")


class DropletMeta:
    """Tuner with droplet algorithm in Ansor.

    Parameters
    ----------
    json_file: str
        json format file
    target:
        hardware target
    log: str
        path to save json file
    trials: int
        number of samples, the default is 100
    pvalue: float
        statistical value to confidence level, the default is 0.05
    """

    def __init__(self, json_file, workload_file, target, log, pvalue=0.05) -> None:
        self.space = Space(json_file, workload_file, target)
        self.final_log = write_file([json_file], log)
        self.pvalue = pvalue
        self.next = [(0, [0] * len(self.space.dims))]
        best_avg = get_ms_time(log)
        self.best_choice = [0, [0] * len(self.space.dims), best_avg]
        self.count, self.execution, self.found_best_pos = 1, 1, True
        self.total_execution = 1
        if len(self.space.dims) > 0:
            self.total_execution = max(self.space.dims)
        self.dims, self.step = self.space.dims, 1
        self.visited, self.batch = set([0]), max(os.cpu_count(), len(self.dims))

    def next_batch(self, batch_size):
        i, json_file_list = 0, []
        # print("size next", len(self.next))
        while i < len(self.next):
            if batch_size > 0 and self.count >= self.trials:
                break
            json_file_list.append(
                self.space.template(values=self.next[i][1], create=False)
            )
            i, self.count = i + 1, self.count + 1
        return self.space.run(json_file_list, self.final_log)

    def has_next(self):
        return len(self.next) > 0 and self.found_best_pos

    def tune(self, n_trial=100):
        self.trials = n_trial
        self.speculation()
        while self.has_next():
            res = self.next_batch(self.batch)
            self.update(res)

    def num_to_bin(self, value, factor=1):
        bin_format = str(0) * (len(self.dims) - len(bin(value)[2:])) + bin(value)[2:]
        return [int(i) * factor for i in bin_format]

    def search_space(self, factor=1):
        search_space = []
        for i in range(0, len(self.space.dims)):
            if len(search_space) > self.batch - len(self.next):
                break
            space = self.num_to_bin(2**i, factor)
            idx = self.space.knob2point(space)
            if idx not in self.visited:
                search_space.append(space)
        return search_space

    def next_pos(self, new_positions):
        "returns the neighbors of the best solution"
        next_set = []
        for p in new_positions:
            new_p = [
                (x + y) % self.dims[i] if (x + y > 0) else 0
                for i, (x, y) in enumerate(zip(p, self.best_choice[1]))
            ]
            idx_p = self.space.knob2point(new_p)
            if idx_p not in self.visited:
                self.visited.add(idx_p)
                next_set.append((idx_p, new_p))
        return next_set

    def p_value(self, elem_1, elem_2):
        if len(elem_1) <= 1 or len(elem_2) <= 1:
            return True
        from scipy import stats  # pylint: disable=import-outside-toplevel

        return stats.ttest_ind(np.array(elem_1), np.array(elem_2)).pvalue <= self.pvalue

    def speculation(self):
        # Gradient descending direction prediction and search space filling
        while len(self.next) < self.batch and self.execution < self.total_execution:
            self.next += self.next_pos(self.search_space(self.execution))
            self.execution += self.step

    def update(self, results):
        self.found_best_pos, count_valids = False, 0
        for i, res in enumerate(results):
            # print(f"{i}, {np.mean(res):.8f}, {self.next[i]}")
            if np.mean(self.best_choice[2]) > np.mean(res):
                self.best_choice = (self.next[i][0], self.next[i][1], res)
                self.found_best_pos = True
            if np.mean(res) != 10000:
                count_valids += 1

        # print(f"best = {np.mean(self.best_choice[2]):.8f}, ({self.best_choice[0]}, {self.best_choice[1]}) ")
        self.next = []

        # stop, because all neighborhoods are invalid.
        if count_valids == 0:
            self.speculation()
            self.found_best_pos = True
            return

        if self.found_best_pos:
            self.next += self.next_pos(self.search_space())
            self.execution = 1
            self.speculation()
