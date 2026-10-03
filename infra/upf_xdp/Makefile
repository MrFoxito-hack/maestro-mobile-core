CLANG ?= clang
CFLAGS = -O2 -g -Wall -Werror -target bpf -I/usr/include/$(shell gcc -dumpmachine)
all: upf_xdp_kern.o
upf_xdp_kern.o: upf_xdp_kern.c
	$(CLANG) $(CFLAGS) -c $< -o $@
