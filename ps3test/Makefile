.SUFFIXES:
ifeq ($(strip $(PSL1GHT)),)
$(error "Please set PSL1GHT in your environment. export PSL1GHT=<path>")
endif

include $(PSL1GHT)/ppu_rules

TARGET    := ARCTEST$(TEST_LIBS)
BUILD     := build$(TEST_LIBS)
SOURCES   := source
DATA      := data
INCLUDES  := include

TITLE     := ARCADIA Test $(TEST_LIBS)
APPID     := ARCT0000$(TEST_LIBS)
CONTENTID := UP0001-$(APPID)_00-0000000000000000
ICON0     := $(CURDIR)/ICON0.PNG
PKGFILES  := $(CURDIR)/pkgfiles

CFLAGS    = -O2 -Wall -mcpu=cell $(MACHDEP) $(INCLUDE) -DTEST_LIBS=$(TEST_LIBS)
CXXFLAGS  = $(CFLAGS)
LDFLAGS   = $(MACHDEP) -Wl,-Map,$(notdir $@).map

LIBS      := -ltiny3d -lfreetype -lpng -lcurl -lmbedtls -lmbedx509 -lmbedcrypto -lz -lpngdec -ljpgdec -lvdec -laudio -lsysmodule -lgcm_sys -lrsx -lsysutil -lio -lnet -lrt -llv2 -lm
LIBDIRS   := $(PORTLIBS)

ifneq ($(BUILD),$(notdir $(CURDIR)))
export OUTPUT   := $(CURDIR)/$(TARGET)
export VPATH    := $(foreach dir,$(SOURCES),$(CURDIR)/$(dir))
export BUILDDIR := $(CURDIR)/$(BUILD)
export DEPSDIR  := $(CURDIR)/$(BUILD)
CFILES   := $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.c)))
export LD       := $(CC)
export OFILES   := $(CFILES:.c=.o)
export INCLUDE  := $(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) $(foreach dir,$(LIBDIRS),-I$(dir)/include) $(LIBPSL1GHT_INC) -I$(CURDIR)/$(BUILD) -I$(PORTLIBS)/include/freetype2
export LIBPATHS := $(foreach dir,$(LIBDIRS),-L$(dir)/lib) $(LIBPSL1GHT_LIB)

.PHONY: $(BUILD) clean pkg
$(BUILD):
	@[ -d $@ ] || mkdir -p $@
	@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile

clean:
	@rm -fr $(BUILD) $(OUTPUT).elf $(OUTPUT).self $(OUTPUT).pkg $(OUTPUT).fake.self

pkg: $(BUILD) $(OUTPUT).pkg
else
DEPENDS := $(OFILES:.o=.d)
$(OUTPUT).self: $(OUTPUT).elf
$(OUTPUT).elf: $(OFILES)
-include $(DEPENDS)
endif
