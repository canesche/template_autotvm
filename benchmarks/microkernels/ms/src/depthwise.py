import os, sys, time, argparse, tvm
from tvm import te, topi
from tvm import meta_schedule as ms
from tvm.meta_schedule.runner.config import EvaluatorConfig
from tvm.script import tir as T

num_threads = os.cpu_count()
os.environ["TVM_NUM_THREADS"] = str(num_threads)
os.environ["MKL_NUM_THREADS"] = str(num_threads * 2 // 3)
os.environ["NUMEXPR_NUM_THREADS"] = str(num_threads * 2 // 3)
os.environ["OMP_NUM_THREADS"] = str(num_threads * 2 // 3)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(SCRIPT_DIR))

from src.utils import *

## ------------------ Global ---------------------
# 128 84 83 83 5  5  2       SAME
# N   CI H  W  KH KW Strides Padding
input_shape = (128, 84, 83, 83)
filter_shape = (84, 1, 5, 5)
strides = (1, 1)
padding = (1, 1)
dilation = (1, 1)
layout = "NCHW"
dtype = "float32"


## ----------------- Benchmark -------------------
def print_depthwise(input_shape, filter_shape, dtype="float32"):
    A = te.placeholder(input_shape, name="A", dtype=dtype)
    B = te.placeholder(filter_shape, name="B", dtype=dtype)
    C = topi.nn.depthwise_conv2d_nhwc(
        A, B, stride=strides, padding=padding, dilation=dilation, out_dtype=dtype
    )
    te.create_prim_func([A, B, C]).show()


@tvm.script.ir_module
class Main:
    @T.prim_func
    def main(
        A: T.Buffer((128, 84, 83, 83), "float32"),
        B: T.Buffer((84, 1, 5, 5), "float32"),
        DepthwiseConv2d: T.Buffer((128, 3, 85, 415), "float32"),
    ):
        T.func_attr({"tir.noalias": T.bool(True)})
        # with T.block("root"):
        PaddedInput = T.alloc_buffer((128, 86, 85, 83))
        for i0, i1, i2, i3 in T.grid(128, 86, 85, 83):
            with T.block("PaddedInput"):
                v_i0, v_i1, v_i2, v_i3 = T.axis.remap("SSSS", [i0, i1, i2, i3])
                T.reads(A[v_i0, v_i1 - 1, v_i2 - 1, v_i3])
                T.writes(PaddedInput[v_i0, v_i1, v_i2, v_i3])
                PaddedInput[v_i0, v_i1, v_i2, v_i3] = T.if_then_else(
                    1 <= v_i1 and v_i1 < 85 and 1 <= v_i2 and v_i2 < 84,
                    A[v_i0, v_i1 - 1, v_i2 - 1, v_i3],
                    T.float32(0),
                )
        for b, i, j, c, di, dj in T.grid(128, 3, 85, 415, 84, 1):
            with T.block("DepthwiseConv2d"):
                v_b, v_i, v_j, v_c, v_di, v_dj = T.axis.remap(
                    "SSSSRR", [b, i, j, c, di, dj]
                )
                T.reads(
                    PaddedInput[v_b, v_i + v_di, v_j + v_dj, v_c // 5],
                    B[v_di, v_dj, v_c // 5, v_c % 5],
                )
                T.writes(DepthwiseConv2d[v_b, v_i, v_j, v_c])
                with T.init():
                    DepthwiseConv2d[v_b, v_i, v_j, v_c] = T.float32(0)
                DepthwiseConv2d[v_b, v_i, v_j, v_c] = (
                    DepthwiseConv2d[v_b, v_i, v_j, v_c]
                    + PaddedInput[v_b, v_i + v_di, v_j + v_dj, v_c // 5]
                    * B[v_di, v_dj, v_c // 5, v_c % 5]
                )


## ---------------------------------------------


def ms_execute(logfile, target, target_name, trials):
    # only print, just to collect the IR module
    # print_depthwise(input_shape, filter_shape, dtype)

    start = time.time()
    database = ms.tune_tir(
        mod=Main,
        target=target,
        max_trials_global=trials,
        num_trials_per_iter=64,
        work_dir=logfile,
        runner=ms.runner.LocalRunner(
            evaluator_config=EvaluatorConfig(
                number=1,
                repeat=1,
                min_repeat_ms=100,
                enable_cpu_cache_flush=True if target_name == "llvm" else False,
            )
        ),
        cost_model=ms.cost_model.XGBModel(
            extractor=ms.feature_extractor.PerStoreFeature(),
            adaptive_training=False,
        ),
        strategy=ms.search_strategy.EvolutionarySearch(),
    )
    end = time.time()

    best_time = get_ms_time(logfile + "/database_tuning_record.json")

    print(f"Best time (ms): {np.mean(best_time)*1000:.10f}")
    print(f"Best std  (ms): {np.std(best_time)*1000:.10f}")
    print(f"Tuning Time (min): {(end-start)/60:.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser("python mm.py -a x86 -l 'results/ms/cpu_matmul'")
    parser.add_argument(
        "-a", "--arch", type=str, required=True, help="Options: x86, aarch64, cuda"
    )
    parser.add_argument("-l", "--logfile", type=str, required=True)
    parser.add_argument("-t", "--trials", type=int, default=1000)
    args = parser.parse_args()

    arch = args.arch
    logfile = args.logfile
    trials = args.trials

    # clean the files
    if os.path.isfile(logfile):
        os.remove(logfile)

    if arch == "x86":
        target_name = "llvm"
        target = tvm.target.Target(f"llvm -num-cores {num_threads // 2}")
        dev = tvm.cpu()
    elif arch == "cuda":
        target_name = "cuda"
        target = tvm.target.Target(
            "cuda -max_threads_per_block 1024 -max_shared_memory_per_block 49152"
        )
        dev = tvm.cuda()
    elif arch == "arm":
        target = tvm.target.Target("llvm -mcpu=a64fx -num-cores 48")
        dev = tvm.cpu()
    else:
        print("Archtecture doesn't support.")
        exit(0)

    ms_execute(logfile, target, target_name, trials)
