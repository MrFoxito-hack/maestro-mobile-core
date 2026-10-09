CLANG ?= clang
CFLAGS = -O2 -g -Wall -Werror -target bpf -I/usr/include/$(shell gcc -dumpmachine)
all: upf_xdp_kern.o urllc_xdp_kern.o
c4: c4_policy_kern.o c4_xdp_kern.o
c4_policy_kern.o: c4_policy_kern.c c4_policy_bpf.h c4_abi.h
	$(CLANG) $(CFLAGS) -c $< -o $@
c4_xdp_kern.o: upf_xdp_kern.c c4_policy_bpf.h c4_abi.h
	$(CLANG) $(CFLAGS) -DMAESTRO_URLLC=1 -DMAESTRO_C4=1 -c $< -o $@
upf_xdp_kern.o: upf_xdp_kern.c
	$(CLANG) $(CFLAGS) -c $< -o $@
urllc_xdp_kern.o: upf_xdp_kern.c
	$(CLANG) $(CFLAGS) -DMAESTRO_URLLC=1 -c $< -o $@
